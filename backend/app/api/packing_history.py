"""Packing evidence review and operational fit feedback."""

from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action
from app.core.revisions import Revision, lock_box_packing
from app.core.values import PackLabel
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.models.packing import Box
from app.models.packing_evidence import PackingFeedback, PackingHistoryReview
from app.models.user import User
from app.modules.packing.history import (
    approval_issues,
    example_out,
    history_data,
    review_out,
    save_review,
    source_current,
)
from app.services.product_identity import ProductIdentityIndex, name_key

router = APIRouter(prefix="/packing-history", tags=["Packing evidence"],
                   dependencies=[Depends(require_action(Action.FLOOR_PACKING))])


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=36)
    pack_size: PackLabel
    pack_count: int = Field(strict=True, gt=0, le=250000)
    net_weight_kg: float = Field(gt=0, le=10000, allow_inf_nan=False)


class HistoryReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(strict=True, ge=0)
    status: Literal["APPROVED", "REJECTED"]
    box_catalogue_id: str = Field(default="", max_length=36)
    route_code: str = Field(default="", max_length=32)
    packing_mode: Literal["EL", "NON_EL"] = "EL"
    items: list[HistoryItem] = Field(default_factory=list, max_length=100)
    verified_fit: bool = False
    source_issues_acknowledged: bool = False
    note: str = Field(min_length=8, max_length=2048)

    @field_validator("note")
    @classmethod
    def meaningful_note(cls, value):
        if len(value.strip()) < 8:
            raise ValueError("Explain the review in at least eight characters")
        return value.strip()


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Revision
    outcome: Literal["FIT", "LARGER_BOX", "EXCESS_SPACE", "REPACKED", "DAMAGE"]
    note: str = Field(default="", max_length=2048)


@router.get("")
def listing(q: str = "", status: Literal["all", "unreviewed", "APPROVED", "REJECTED"] = "all",
            limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db, scope="function")):
    data = history_data()
    reviews = {r.example_id: r for r in db.query(PackingHistoryReview).all()}
    index = ProductIdentityIndex(db.query(Product).filter_by(is_active=True).all())
    boxes = db.query(BoxCatalogue).all()
    term = name_key(q)
    matching_products = {product.id for product in index.products.values()
                         if term and term in name_key(" ".join([product.t_code, product.name_th or "", product.name_vi or "", product.name_en or ""]))}
    examples = []
    for example in data["examples"]:
        review = reviews.get(example["id"])
        current = review is not None and source_current(review) and not approval_issues(review, index.products, boxes)
        if status == "unreviewed" and current:
            continue
        if status in {"APPROVED", "REJECTED"} and (not current or review.status != status):
            continue
        values = [example["shipment"], example["subcustomer"], example["box_name"],
                  *(str(item.get(key) or "") for item in example["items"] for key in ("code", "name_th", "name_vi", "name_en"))]
        if term and term not in name_key(" ".join(values)):
            mapped = {item["product_id"] for item in review.definition.get("items", [])} if review else set()
            if not matching_products.intersection(mapped) and not any(
                matching_products.intersection(index.match(item, raw_code=item["code"]).candidate_ids)
                for item in example["items"]
            ):
                continue
        examples.append(example)
    counts = Counter(r.status for r in reviews.values() if source_current(r) and not approval_issues(r, index.products, boxes))
    return {"source_version": data["version"], "summary": {**data["summary"],
            "approved": counts["APPROVED"], "rejected": counts["REJECTED"]},
            "total": len(examples), "has_more": offset + limit < len(examples),
            "items": [example_out(example, index=index, boxes=boxes, review=reviews.get(example["id"]))
                      for example in examples[offset:offset + limit]]}


@router.put("/{example_id}/review")
def review(example_id: str, payload: HistoryReviewInput,
           db: Session = Depends(get_db, scope="function"),
           actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    if payload.status == "APPROVED" and (not payload.route_code.strip() or not payload.items):
        raise HTTPException(422, "Choose a route and map every source item")
    try:
        return review_out(save_review(db, example_id, payload, actor))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(409, "Someone reviewed this example; reload before saving") from exc


@router.get("/feedback/summary")
def feedback_summary(db: Session = Depends(get_db, scope="function")):
    counts = dict(db.query(PackingFeedback.outcome, func.count()).group_by(PackingFeedback.outcome).all())
    return {"outcomes": counts, "observations": sum(counts.values()),
            "boxes": db.query(func.count(func.distinct(PackingFeedback.box_id))).scalar(),
            "population": "Only boxes with recorded feedback; not all packed boxes"}


@router.post("/boxes/{box_id}/feedback")
def feedback(box_id: str, payload: FeedbackInput,
             db: Session = Depends(get_db, scope="function"),
             actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    order = lock_box_packing(db, box_id, payload.expected_revision)
    box = db.query(Box).filter_by(id=box_id, client_order_id=order.id).with_for_update().populate_existing().one()
    note = payload.note.strip()
    if payload.outcome != "FIT" and len(note) < 5:
        raise HTTPException(422, "Describe the fit issue or packing adjustment")
    revision = box.order.packing_revision
    previous = db.query(PackingFeedback).filter_by(box_id=box.id, packing_revision=revision, outcome=payload.outcome).first()
    if previous:
        if previous.outcome == payload.outcome and previous.note == note:
            return {"id": previous.id, "outcome": previous.outcome, "replayed": True}
        raise HTTPException(409, "This result already has feedback for this packing revision; retain the original observation")
    snapshot = {"proposed": (box.packing_snapshot or {}).get("initial_plan"),
                "planning_evidence": box.packing_snapshot,
                "actual": {"catalogue_id": box.override_box_catalogue_id or box.box_catalogue_id,
                           "verified_net_weight_kg": float(box.actual_net_weight_kg) if box.actual_net_weight_kg is not None else None,
                           "items": [{"product_id": item.product_id, "pack_size": item.pack_size,
                                      "pack_count": item.final_packed_count, "net_weight_kg": float(item.final_net_weight_kg)}
                                     for item in box.items]}}
    observation = PackingFeedback(box_id=box.id, packing_revision=revision, outcome=payload.outcome,
                                  note=note, snapshot=snapshot, actor_id=actor.id)
    db.add(observation)
    db.flush()
    db.add(AuditLog(event_type="PACKING_FIT_RECORDED", actor_id=actor.id, actor_role=actor.role.value,
                    target_table="packing_feedback", target_id=observation.id,
                    after_state={"outcome": payload.outcome, "box_id": box.id, "packing_revision": revision}))
    return {"id": observation.id, "outcome": observation.outcome, "replayed": False}
