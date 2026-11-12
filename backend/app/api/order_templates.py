"""Review format mappings, save a new immutable version, and explicitly re-read intake."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.file_storage import source_path
from app.core.parser_process import parse_document
from app.core.policy import Action
from app.models.audit import AuditLog
from app.models.excel_template import ExcelTemplate
from app.models.order import OrderStatus, RawOrderFile
from app.models.user import User
from app.modules.orders.acknowledgment import order_drafts, prepare_acknowledgment
from app.modules.orders.quantity_review import draft_revision
from app.modules.orders.templates import template_issues, template_snapshots
from app.services.canonical_schema import CanonicalOrderDraft

router = APIRouter(prefix="/order-templates", tags=["Customer Excel formats"])


class Confirmation(BaseModel):
    template_id: str
    expected_version: int = Field(ge=1)
    expected_revision: str = Field(min_length=64, max_length=64)
    default_unit: Literal["kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"] | None = None
    definition: dict | None = None


def pending(db, raw_file_id):
    raw = (
        db.query(RawOrderFile)
        .filter_by(id=raw_file_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not raw:
        raise HTTPException(404, "Order file not found")
    if raw.status not in {OrderStatus.UPLOADED, OrderStatus.PARSED}:
        raise HTTPException(
            409,
            "Re-reading is available only before procurement; confirmed orders cannot be replaced",
        )
    return raw


def response(db, raw):
    drafts = order_drafts(raw)
    return {
        "revision": draft_revision(drafts),
        "formats": template_snapshots(db, raw.main_customer_id),
        "detected": [d.metadata.get("template") for d in drafts if d.metadata.get("template")],
        "issues": template_issues(drafts),
    }


@router.get("/{raw_file_id}")
def review(
    raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    return response(db, pending(db, raw_file_id))


def validate_definition(definition):
    if len(json.dumps(definition)) > 40000:
        raise HTTPException(422, "Format definition is too large")
    if definition.get("kind") not in {"table", "text"} or definition.get(
        "numeric_pack_unit"
    ) not in {None, "g", "kg"}:
        raise HTTPException(422, "Select a supported layout and packing unit")
    if definition.get("text_quantity_separator") not in {None, "-"}:
        raise HTTPException(422, "Select a supported text quantity separator")
    review_issues = definition.get("review_issues", [])
    if not isinstance(review_issues, list) or len(review_issues) > 30 or any(
        not isinstance(issue, str) or not 1 <= len(issue) <= 512 for issue in review_issues
    ):
        raise HTTPException(422, "Use short format review notes")
    tokens = definition.get("identity_tokens", [])
    if (
        not isinstance(tokens, list)
        or len(tokens) > 30
        or any(not isinstance(t, str) or not 1 <= len(t) <= 128 for t in tokens)
    ):
        raise HTTPException(422, "Customer identity markers must be short text values")
    sheets = definition.get("sheets", [])
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 20:
        raise HTTPException(422, "Choose between one and twenty order sheets")
    for sheet in sheets:
        if (
            not isinstance(sheet, dict)
            or not isinstance(sheet.get("anchors"), dict)
            or (sheet.get("customers") is not None and not isinstance(sheet["customers"], dict))
        ):
            raise HTTPException(422, "Choose valid sheet headers and customer columns")
        fields = sheet.get("fields", {})
        if not isinstance(fields, dict) or not isinstance(fields.get("alt", []), list):
            raise HTTPException(422, "Choose valid product columns")
        columns = [v for v in fields.values() if isinstance(v, int)] + fields.get("alt", [])
        ignored = sheet.get("ignored_columns", {})
        if not isinstance(ignored, dict) or len(ignored) > 512 or any(
            not isinstance(label, str) or len(label.strip()) > 256 for label in ignored.values()
        ):
            raise HTTPException(422, "Choose valid ignored column headings")
        try:
            columns += [int(c) for c in sheet.get("anchors", {})] + [
                int(c) for c in (sheet.get("customers") or {})
            ]
            columns += [int(c) for c in ignored]
        except (TypeError, ValueError):
            raise HTTPException(422, "Use valid column numbers") from None
        if any(type(c) is not int or not 1 <= c <= 512 for c in columns):
            raise HTTPException(422, "Columns must be between 1 and 512")
        if len(set(fields.get("alt", []))) != len(fields.get("alt", [])):
            raise HTTPException(422, "Alternate product columns must be unique")
        allowed = {
            "code",
            "supplier_code",
            "customer_product_code",
            "name",
            "pack",
            "quantity",
            "unit",
            "adjacent",
            "description_pack",
            "alt",
            "notes",
        }
        if set(fields) - allowed or any(
            type(v) is not int for k, v in fields.items() if k != "alt"
        ):
            raise HTTPException(422, "Choose valid column numbers")
        if any(
            not isinstance(v, str) or not 1 <= len(v.strip()) <= 256
            for v in sheet.get("anchors", {}).values()
        ):
            raise HTTPException(422, "Header checks need nonempty text")
        if any(
            not isinstance(v, str) or not 1 <= len(v.strip()) <= 128
            for v in (sheet.get("customers") or {}).values()
        ):
            raise HTTPException(422, "Subcustomer columns need valid customer codes")
        if (
            not fields.get("name")
            or not sheet.get("anchors")
            or not (fields.get("quantity") or sheet.get("customers"))
        ):
            raise HTTPException(
                422, "Each sheet needs header anchors, a product name and order columns"
            )


@router.post("/{raw_file_id}/confirm")
def confirm(
    raw_file_id: str,
    payload: Confirmation,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    raw = pending(db, raw_file_id)
    drafts = order_drafts(raw)
    if draft_revision(drafts) != payload.expected_revision:
        raise HTTPException(409, "Order review changed. Reload before re-reading the file")
    snapshots = template_snapshots(db, raw.main_customer_id)
    selected = next((s for s in snapshots if s["id"] == payload.template_id), None)
    if not selected or selected["version"] != payload.expected_version:
        raise HTTPException(409, "Format version changed. Reload its latest version")
    # Keep account/customer identity owned by the selected reference, not arbitrary input.
    definition = {**selected["definition"], **(payload.definition or {})}
    for field in ("key", "main_customer_code", "client_code"):
        definition[field] = selected["definition"][field]
    definition["default_unit"] = payload.default_unit
    validate_definition(definition)
    latest = db.query(ExcelTemplate).filter_by(id=selected["id"]).one()
    if latest.account_key != (raw.main_customer_id or "GENERAL"):
        raise HTTPException(422, "Selected template belongs to a different main customer account")
    version = ExcelTemplate(
        account_key=latest.account_key,
        template_key=latest.template_key,
        version=latest.version + 1,
        definition=definition,
        confirmed=True,
        confirmed_by=actor.id,
    )
    db.add(version)
    db.flush()
    replacement = dict(
        id=version.id, version=version.version, confirmed=True, definition=definition
    )
    snapshots = [replacement if s["id"] == selected["id"] else s for s in snapshots]
    with source_path(raw) as path:
        parsed = parse_document(
            str(path),
            mode="universal",
            shipment_date=raw.shipment_target_date,
            profiles=snapshots,
            original_name=raw.file_name,
            selected_key=definition["key"],
        )
    updated = [CanonicalOrderDraft.model_validate(d) for d in parsed["drafts"]]
    if any(d.metadata.get("template", {}).get("status") != "MATCHED" for d in updated):
        raise HTTPException(
            422,
            "The selected mapping does not match the workbook. Correct its header anchors and columns",
        )
    if not payload.default_unit and any(
        i.unit == "unknown" and not i.quantity_issues
        for d in updated
        for i in d.items
        if i.quantity > 0
    ):
        raise HTTPException(
            422, "Confirm a default quantity unit for this format's unlabeled order amounts"
        )
    # The saved mapping belongs to the selected billing account. Its reference
    # workbook's customer label may be a trading name, alias, or shop name.
    # Comparing that text to a billing account code incorrectly rejected valid
    # account-scoped mappings such as TONIO-MAIN / HUNGFAT-MATRIX.
    raw.acknowledgment_summary = {
        **(raw.acknowledgment_summary or {}),
        "drafts": [d.model_dump(mode="json") for d in updated],
        "validation": {"can_split": False},
    }
    prepare_acknowledgment(raw, updated)
    db.add(
        AuditLog(
            event_type="EXCEL_FORMAT_CONFIRMED",
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="raw_order_files",
            target_id=raw.id,
            before_state={"revision": payload.expected_revision, "template_id": selected["id"]},
            after_state={
                "template_id": version.id,
                "version": version.version,
                "revision": draft_revision(updated),
            },
        )
    )
    db.flush()
    return response(db, raw)
