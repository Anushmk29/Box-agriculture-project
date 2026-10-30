"""Revision-checked partial edits for the six-column packing worksheet."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action
from app.models.audit import AuditLog
from app.models.master_data import (
    BoxCatalogue,
    BoxMaterial,
    ControlStatus,
    Firmness,
    ProduceType,
    Product,
    ProductBoxLimit,
    TemperatureZone,
    WeightClass,
)
from app.models.user import User
from app.modules.packing.catalogue import reference_for
from app.modules.packing.materials import material_for_temperature
from app.modules.packing.product_table import ENUMS, apply_reconciliation, row_out, table_problems

router = APIRouter(
    prefix="/table",
    dependencies=[Depends(require_action(Action.FLOOR_PACKING))],
    tags=["Packing setup"],
)


class TableLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    box_catalogue_id: str
    max_net_weight_kg: float = Field(gt=0, le=10000, allow_inf_nan=False)


class Changes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    box_material: BoxMaterial | None = None
    foam_below_kg: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    temperature: TemperatureZone | None = None
    control_status: ControlStatus | None = None
    produce_type: ProduceType | None = None
    weight_class: WeightClass | None = None
    firmness: Firmness | None = None
    volume_l_per_kg: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    grouping_row: int | None = None
    identity_row: int | None = None
    limits: list[TableLimit] | None = Field(default=None, max_length=15)
    # Explicit acknowledgement for notes that cannot be converted to kg.
    limits_guidance_only: bool = False
    limit_resolutions: dict[str, str] | None = None
    confirm: bool = False


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    expected_revision: int = Field(ge=1)
    changes: dict


class Bulk(BaseModel):
    edits: list[Edit] = Field(min_length=1, max_length=100)


class NewProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_code: str = Field(min_length=1, max_length=32)
    name_en: str = Field(min_length=1, max_length=256)
    name_th: str = Field(default="", max_length=256)
    changes: Changes = Field(default_factory=Changes)


def update(db, product, changes, actor):
    before = row_out(product)
    source = dict(product.packing_source or {})
    table = dict(source.get("table", {}))
    manual = set(table.get("manual_fields", []))
    unresolved = set(table.get("unresolved", []))
    provided = set(changes.model_fields_set)
    if "temperature" in provided or "box_material" in provided:
        temperature = changes.temperature if "temperature" in provided else (
            None if "temperature" in unresolved else product.temperature
        )
        if temperature is None:
            raise ValueError("Choose the product temperature before its box material")
        required_material = material_for_temperature(temperature)
        if "box_material" in provided and changes.box_material != required_material:
            raise ValueError("Cold products require foam; normal/ambient products require cardboard")
        changes.temperature = temperature
        changes.box_material = required_material
        provided.update({"temperature", "box_material"})
    if "foam_below_kg" in provided:
        product.foam_below_kg = changes.foam_below_kg
        manual.add("foam_below_kg")
    if "identity_row" in provided:
        identity = next(
            (r for r in table.get("code_rows", []) if r["row"] == changes.identity_row), None
        )
        if not identity:
            raise ValueError("Choose an existing Product Codes row")
        product.name_en = identity["name_en"] or product.name_en
        product.name_th = identity["name_th"] or product.name_th
        manual.add("identity")
        unresolved.discard("identity")
    if "grouping_row" in provided:
        if changes.grouping_row not in table.get("grouping_rows", []):
            raise ValueError("Choose a matching grouping row")
        table["grouping_row"] = changes.grouping_row
        table["manual_fields"] = sorted(manual)
        source["table"] = table
        product.packing_source = source
        apply_reconciliation(db, product, db.query(BoxCatalogue).all())
        table = dict(product.packing_source["table"])
        unresolved = set(table["unresolved"])
    for key in [
        "box_material",
        "temperature",
        "control_status",
        "produce_type",
        "weight_class",
        "firmness",
    ]:
        if key not in provided:
            continue
        value = getattr(changes, key)
        if value is None and key != "firmness":
            raise ValueError(f"Choose a value for {key.replace('_', ' ')}")
        setattr(product, key, value.value if key == "firmness" and value is not None else value)
        manual.add(key)
        unresolved.discard(key)
    if "volume_l_per_kg" in provided:
        product.packing_volume_l_per_kg = changes.volume_l_per_kg
        product.packing_volume_estimated = False
        manual.add("volume")
    if "limits" in provided:
        limits = changes.limits or []
        if len({limit.box_catalogue_id for limit in limits}) != len(limits):
            raise ValueError("Use one limit per box")
        catalogues = {
            b.id: b for b in db.query(BoxCatalogue).order_by(BoxCatalogue.id).with_for_update()
        }
        for limit in limits:
            box = catalogues.get(limit.box_catalogue_id)
            if not box or not reference_for(box) or box.box_type != product.box_material:
                raise ValueError("Limits must use approved boxes of the product's material")
        for limit in list(product.box_limits):
            db.delete(limit)
        db.flush()
        for limit in limits:
            db.add(
                ProductBoxLimit(
                    product_id=product.id,
                    **limit.model_dump(),
                    reviewed=False,
                    source_note="Staff table correction",
                )
            )
        db.flush()
        db.expire(product, ["box_limits"])
        manual.add("limits")
        if not table.get("limit_issues"):
            unresolved.discard("limits")
    if changes.limit_resolutions:
        resolutions = {**table.get("limit_resolutions", {}), **changes.limit_resolutions}
        issues = {issue["note"]: issue for issue in table.get("limit_issues", [])}
        boxes = {box.id: box for box in db.query(BoxCatalogue).all()}
        for note, identity in changes.limit_resolutions.items():
            box = boxes.get(identity)
            issue = issues.get(note)
            if (
                not issue
                or issue["max_net_weight_kg"] is None
                or not box
                or not reference_for(box)
                or box.box_type != product.box_material
            ):
                raise ValueError(
                    "Map each kilogram note to an approved box of the product material"
                )
            limit = next(
                (limit for limit in product.box_limits if limit.box_catalogue_id == identity), None
            )
            if limit is None:
                db.add(
                    ProductBoxLimit(
                        product=product,
                        box_catalogue_id=identity,
                        max_net_weight_kg=issue["max_net_weight_kg"],
                        reviewed=False,
                        source_note=note,
                    )
                )
            else:
                limit.max_net_weight_kg = min(
                    float(limit.max_net_weight_kg), issue["max_net_weight_kg"]
                )
        table["limit_resolutions"] = resolutions
        manual.add("limits")
        if all(note in resolutions for note in issues):
            unresolved.discard("limits")
    if changes.limits_guidance_only:
        manual.add("limits")
        unresolved.discard("limits")
        table["limits_guidance_only"] = True
    table.update(manual_fields=sorted(manual), unresolved=sorted(unresolved))
    if "fields" in table:
        table["fields"] = {
            key: None if key in unresolved else enum(getattr(product, key)).value
            for key, enum in ENUMS.items()
        }
    source = dict(product.packing_source or source)
    source["table"] = table
    product.packing_source = source
    product.packing_reviewed = False
    product.packing_volume_reviewed = False
    if changes.confirm:
        missing = [p for p in table_problems(product) if p != "confirmation"]
        if missing:
            raise ValueError("Complete or resolve: " + ", ".join(missing))
        product.packing_reviewed = True
        product.packing_volume_reviewed = True
        for limit in product.box_limits:
            limit.reviewed = True
    product.packing_revision += 1
    db.flush()
    after = row_out(product)
    db.add(
        AuditLog(
            event_type="PRODUCT_TABLE_UPDATED",
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="products",
            target_id=product.id,
            before_state=before,
            after_state=after,
        )
    )
    return after


@router.get("")
def listing(
    q: str = "",
    status: Literal["all", "ready", "review"] = "all",
    el: Literal["all", "EL", "COMBINABLE", "unknown"] = "all",
    temperature: Literal["all", "COLD", "NORMAL", "unknown"] = "all",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db, scope="function"),
):
    products = (
        db.query(Product)
        .filter_by(is_active=True)
        .options(selectinload(Product.box_limits))
        .order_by(Product.t_code)
        .all()
    )
    rows = [row_out(p) for p in products]
    total = len(rows)
    ready = sum(r["ready"] for r in rows)
    temperature_counts = {
        "cold": sum(r["temperature"] == "COLD" for r in rows),
        "ambient": sum(r["temperature"] == "NORMAL" for r in rows),
        "temperature_unknown": sum(r["temperature"] is None for r in rows),
        "grouping_matched": sum(bool(r["source_choices"]) for r in rows),
    }
    term = q.strip().casefold()
    rows = [
        r
        for r in rows
        if (not term or term in " ".join([r["t_code"], r["name_en"], r["name_th"]]).casefold())
        and (status == "all" or r["ready"] == (status == "ready"))
        and (el == "all" or r["control_status"] == (None if el == "unknown" else el))
        and (temperature == "all" or r["temperature"] == (None if temperature == "unknown" else temperature))
    ]
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "has_more": offset + limit < len(rows),
        "summary": {"total": total, "ready": ready, "strict": settings.PACKING_PROFILES_ENABLED, **temperature_counts},
    }


@router.post("/bulk")
def bulk(
    payload: Bulk,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    if len({e.id for e in payload.edits}) != len(payload.edits):
        raise HTTPException(422, "Each product may occur only once")
    results = []
    # Consistent product lock order prevents concurrent bulk saves deadlocking.
    products = {
        product.id: product
        for product in db.query(Product)
        .filter(Product.id.in_([edit.id for edit in payload.edits]), Product.is_active.is_(True))
        .order_by(Product.id)
        .with_for_update()
        .populate_existing()
        .all()
    }
    for edit in sorted(payload.edits, key=lambda e: e.id):
        product = products.get(edit.id)
        if not product:
            results.append({"id": edit.id, "error": "Product no longer available", "status": 404})
        elif product.packing_revision != edit.expected_revision:
            results.append(
                {
                    "id": edit.id,
                    "error": "Changed by another staff member. Reload this row and reapply your changes.",
                    "status": 409,
                    "current": row_out(product),
                }
            )
        else:
            try:
                with db.begin_nested():
                    result = update(db, product, Changes.model_validate(edit.changes), actor)
                results.append({"id": edit.id, "product": result})
            except ValidationError as exc:
                results.append(
                    {
                        "id": edit.id,
                        "error": "; ".join(
                            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                            for error in exc.errors()
                        ),
                        "status": 422,
                    }
                )
            except ValueError as exc:
                results.append({"id": edit.id, "error": str(exc), "status": 422})
    return {"results": results}


@router.post("", status_code=201)
def create(
    payload: NewProduct,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    code, name = payload.t_code.strip().upper(), payload.name_en.strip()
    if not code or not name:
        raise HTTPException(422, "Enter a SKU and English name")
    if db.query(Product.id).filter(func.upper(Product.t_code) == code).first():
        raise HTTPException(409, "This SKU already exists; edit the existing row")
    product = Product(
        t_code=code,
        name_en=name,
        name_th=payload.name_th.strip(),
        name_vi=name,
        box_material="FOAM",
        temperature="COLD",
        control_status="COMBINABLE",
        produce_type="OTHER",
        weight_class="LIGHT",
        default_pack_sizes=[],
        packing_source={
            "table": {
                "manual_fields": ["identity"],
                "unresolved": ["box_material", "temperature", "control_status", "weight_class"],
            }
        },
    )
    db.add(product)
    try:
        db.flush()
        return update(db, product, payload.changes, actor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(409, "This SKU already exists") from exc
