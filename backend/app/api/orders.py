from pathlib import Path
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field
from pypdf.errors import PyPdfError
from sqlalchemy.orm import Session, joinedload
from xlrd.biffh import XLRDError

from app.core.database import get_db
from app.core.dates import canonical_day
from app.core.dependencies import require_access, require_action
from app.core.file_storage import (
    MIME,
    UploadTooLarge,
    persist_source,
    safe_filename,
    source_path,
    stage_upload,
)
from app.core.idempotency import run_once, validate_request_key
from app.core.parser_process import parse_document
from app.core.policy import Action
from app.core.read_models import LegacyPage, bounded_raw
from app.core.work_limits import WorkLimit
from app.models.audit import AuditLog
from app.models.master_data import Customer
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.request_result import RequestResult
from app.models.user import User, UserRole
from app.modules.orders.acknowledgment import (
    acknowledgment_csv,
    order_drafts,
    prepare_acknowledgment,
)
from app.modules.orders.intake import OrderIntake
from app.modules.orders.quantities import pack_key, unit_key
from app.modules.orders.quantity_review import draft_revision, quantity_review_data, quantity_rows
from app.modules.orders.validation import OrderValidationService
from app.modules.procurement.batch import PurchasingBatchService
from app.modules.workflow.service import WorkflowService
from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator
from app.services.canonical_schema import (
    ActionType,
    CanonicalDraftItem,
    CanonicalOrderDraft,
    IngestionChannel,
)

router = APIRouter(prefix="/orders", tags=["Stage 01: Order Intake & Splitting"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}
ALLOWED_UNIVERSAL_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".pdf"}


class UniversalUploadResponse(BaseModel):
    templates: list[dict] = Field(default_factory=list)
    message: str
    raw_file_id: str
    file_name: str
    channel: str
    total_sub_customers_detected: int
    created_orders_count: int
    total_items_processed: int
    matched_items_count: int
    unmatched_items_count: int
    match_rate_percentage: float
    total_net_weight_kg: float
    procurement_consolidated_skus: int


class OrderAcknowledgmentResponse(BaseModel):
    templates: list[dict] = Field(default_factory=list)
    message: str
    raw_file_id: str
    file_name: str
    main_customer_id: str | None
    total_sub_customers_detected: int
    sub_customer_codes: list[str]
    sheets_detected: list[dict]
    group_summary: dict | None = None
    sub_customer_details: list[dict] | None = None
    status: str
    verification_notice: str


class OrderSplitResponse(BaseModel):
    raw_file_id: str
    batch_id: str
    status: str
    total_client_orders_created: int
    total_line_items_created: int
    total_estimated_weight_kg: float
    sample_orders: list[dict]


class IntakeQueueItem(BaseModel):
    raw_file_id: str
    file_name: str
    shipment_target_date: str
    route_line: str
    status: str
    total_sub_customers_detected: int
    unresolved_product_rows: int
    can_split: bool


def acknowledgment_response(raw_file: RawOrderFile) -> OrderAcknowledgmentResponse:
    summary = raw_file.acknowledgment_summary or {}
    return OrderAcknowledgmentResponse(
        templates=[d.get("metadata", {}).get("template") for d in summary.get("drafts", []) if d.get("metadata", {}).get("template")],
        message="Order matrix is ready for review.",
        raw_file_id=raw_file.id,
        file_name=raw_file.file_name,
        main_customer_id=raw_file.main_customer_id,
        total_sub_customers_detected=summary.get("total_sub_customers", 0),
        sub_customer_codes=summary.get("sub_customer_codes", []),
        sheets_detected=summary.get("sheets_processed", []),
        group_summary=summary.get("group_summary", {}),
        sub_customer_details=summary.get("sub_customer_details", []),
        status=raw_file.status.value,
        verification_notice=summary.get(
            "verification_notice", "Review this intake file before confirming the split."
        ),
    )


@router.get("/intake-queue", response_model=list[IntakeQueueItem])
def intake_queue(
    response: Response,
    page: LegacyPage = Depends(),
    db: Session = Depends(get_db, scope="function"),
    _actor: User = Depends(require_access(UserRole.GENERAL)),
):
    query = (db.query(RawOrderFile, RawOrderFile.acknowledgment_summary["validation"]["unresolved_product_rows"].as_integer(),
        RawOrderFile.acknowledgment_summary["validation"]["can_split"].as_boolean()).filter(RawOrderFile.status.in_({OrderStatus.UPLOADED, OrderStatus.PARSED}))
        .order_by(RawOrderFile.created_at.desc(), RawOrderFile.id))
    pending, _ = page.rows(query, response, replacement="/orders/files")
    return [
        IntakeQueueItem(
            raw_file_id=raw.id,
            file_name=raw.file_name,
            shipment_target_date=raw.shipment_target_date,
            route_line=raw.route_line,
            status=raw.status.value,
            total_sub_customers_detected=raw.detected_sub_customer_count or 0,
            unresolved_product_rows=unresolved or 0,
            can_split=bool(can_split),
        )
        for raw, unresolved, can_split in pending
    ]


@router.get("/intake-queue/{raw_file_id}", response_model=OrderAcknowledgmentResponse)
def resume_intake(
    raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    _actor: User = Depends(require_access(UserRole.GENERAL)),
):
    raw_file = bounded_raw(db, raw_file_id)
    if not raw_file or raw_file.status not in {OrderStatus.UPLOADED, OrderStatus.PARSED}:
        raise HTTPException(status_code=404, detail="Pending intake file not found")
    return acknowledgment_response(raw_file)


class AdHocItemInput(BaseModel):
    t_code: str = Field(min_length=1, max_length=64)
    name: str | None = None
    pack_size: str = Field(default="100g", min_length=1, max_length=64)
    pack_count: int = Field(default=1, gt=0)
    estimated_kg: float | None = Field(default=None, gt=0)


class AdHocOrderRequest(BaseModel):
    client_code: str = Field(min_length=1, max_length=64)
    route_group: str | None = None
    source_notes: str | None = "Manual order entry"
    items: list[AdHocItemInput] = Field(min_length=1, max_length=200)


class OrderTemplateInfo(BaseModel):
    id: str
    name: str
    filename: str
    description: str
    format: str
    file_size_bytes: int
    download_url: str


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"
FALLBACK_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "outputs" / "order-input-templates"

TEMPLATE_CATALOG = {
    "group-matrix": {
        "id": "group-matrix",
        "name": "Group Order Matrix Template",
        "filename": "Group_Order_Matrix_Template.xlsx",
        "description": "Multi-customer route matrix with group verification, floating anchors, and sub-customer columns (Tonio / NGA format).",
        "format": "XLSX",
        "download_url": "/api/v3/orders/templates/group-matrix",
    },
    "single-customer": {
        "id": "single-customer",
        "name": "Single Customer Order Template",
        "filename": "Single_Customer_Order_Template.xlsx",
        "description": "Tabular purchase order for individual customer shipments with SKU codes, pack sizes, and multilingual produce names.",
        "format": "XLSX",
        "download_url": "/api/v3/orders/templates/single-customer",
    },
}


def _resolve_template_path(filename: str) -> Path | None:
    primary = TEMPLATES_DIR / filename
    if primary.exists():
        return primary
    fallback = FALLBACK_TEMPLATES_DIR / filename
    if fallback.exists():
        return fallback
    return None


@router.get("/templates", response_model=list[OrderTemplateInfo])
def list_order_templates(_actor: User = Depends(require_access(UserRole.GENERAL))):
    """
    Returns available order intake and group verification templates for download.
    """
    results = []
    for item in TEMPLATE_CATALOG.values():
        file_path = _resolve_template_path(item["filename"])
        file_size = file_path.stat().st_size if file_path and file_path.exists() else 0
        results.append(
            OrderTemplateInfo(
                id=item["id"],
                name=item["name"],
                filename=item["filename"],
                description=item["description"],
                format=item["format"],
                file_size_bytes=file_size,
                download_url=item["download_url"],
            )
        )
    return results


@router.get("/templates/{template_id}")
def download_order_template(
    template_id: str,
    _actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Downloads the requested Excel order intake or group matrix template.
    """
    key = template_id.lower().replace("_", "-").replace(".xlsx", "")
    if key in ["group-matrix", "group-order-matrix-template", "matrix"]:
        item = TEMPLATE_CATALOG["group-matrix"]
    elif key in ["single-customer", "single-customer-order-template", "single"]:
        item = TEMPLATE_CATALOG["single-customer"]
    elif template_id in TEMPLATE_CATALOG:
        item = TEMPLATE_CATALOG[template_id]
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_id}' not found. Available: {list(TEMPLATE_CATALOG.keys())}",
        )

    file_path = _resolve_template_path(item["filename"])
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Template file not found on server")

    return FileResponse(
        path=str(file_path),
        filename=item["filename"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{item["filename"]}"'},
    )


@router.post("/upload-matrix", response_model=OrderAcknowledgmentResponse)
def upload_order_matrix(
    file: UploadFile = File(...),
    shipment_date: str = Form(...),
    route_line: str = Form(..., min_length=1, max_length=32),
    main_customer_id: str | None = Form(None),
    request_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Intake & Acknowledgment:
    Main customer uploads an order matrix. The system immediately discovers and returns
    the total sub-customer count and sheet breakdown for instant human verification.
    """
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    try:
        shipment_date = canonical_day(shipment_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if suffix not in ALLOWED_WORKBOOK_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail="A valid Excel .xlsx or .xlsm workbook is required",
        )
    if not main_customer_id:
        raise HTTPException(
            status_code=422,
            detail="Select the main customer account before uploading the workbook",
        )
    main_customer = (
        db.query(Customer)
        .filter(
            Customer.id == main_customer_id,
            Customer.is_main_customer.is_(True),
        )
        .first()
    )
    if not main_customer:
        raise HTTPException(status_code=422, detail="The selected main customer is invalid")

    validate_request_key(request_key)
    try:
        original_name = safe_filename(original_name)
        with stage_upload(file.file, original_name) as content:
            def apply():
                try:
                    from app.core.config import settings
                    from app.modules.orders.templates import template_snapshots
                    options = {"profiles": template_snapshots(db, main_customer_id), "original_name": original_name} if settings.EXCEL_TEMPLATE_PROFILES_ENABLED else {}
                    parsed = parse_document(str(content.path), mode="universal" if options else "matrix", **options)
                    ack_info = parsed["acknowledgment"] or {"total_sub_customers": len(parsed["drafts"])}
                    drafts = [CanonicalOrderDraft.model_validate(value) for value in parsed["drafts"]]
                except WorkLimit:
                    raise
                except (InvalidFileException, BadZipFile, ValueError, OSError) as exc:
                    raise HTTPException(422, "The uploaded Excel workbook is invalid or cannot be parsed") from exc
                uri = persist_source(db, content)
                raw_file = RawOrderFile(file_name=original_name, storage_url=uri,
                    file_sha256=content.sha256, file_size_bytes=content.size,
                    shipment_target_date=shipment_date, route_line=route_line,
                    uploaded_by=actor.id, main_customer_id=main_customer_id,
                    detected_sub_customer_count=ack_info["total_sub_customers"],
                    acknowledgment_summary={**ack_info, "drafts": [draft.model_dump(mode="json") for draft in drafts]},
                    status=OrderStatus.UPLOADED)
                db.add(raw_file)
                db.flush()
                prepare_acknowledgment(raw_file, drafts)
                db.flush()
                return acknowledgment_response(raw_file).model_dump(mode="json")
            return run_once(db, actor_id=actor.id, operation="orders.upload_matrix.v1", key=request_key,
                payload={"filename": original_name, "sha256": content.sha256, "size": content.size,
                    "shipment_date": shipment_date, "route_line": route_line, "main_customer_id": main_customer_id}, execute=apply)
    except WorkLimit:
        raise
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "The uploaded workbook is empty or has an invalid name") from exc


@router.post("/upload-universal", response_model=UniversalUploadResponse)
def upload_order_file_universal(
    file: UploadFile = File(...),
    shipment_date: str = Form(...),
    route_line: str = Form("GENERAL", min_length=1, max_length=32),
    main_customer_id: str | None = Form(None),
    request_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Universal Multi-Modal Intake:
    Ingests Excel (.xlsx, .xlsm, .xls) and PDF purchase orders.
    Directly extracts sub-customers, applies standard template weights and units, and generates atomic orders.
    """
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    try:
        shipment_date = canonical_day(shipment_date)
        if main_customer_id:
            OrderIntake(db).require_main_customer(main_customer_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if suffix not in ALLOWED_UNIVERSAL_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{suffix}'. Supported: .xlsx, .xlsm, .xls, .pdf",
        )

    validate_request_key(request_key)
    try:
        original_name = safe_filename(original_name)
        with stage_upload(file.file, original_name) as content:
            def apply():
                try:
                    res = AdaptiveOrderOrchestrator(db).ingest_file(file_path=str(content.path),
                        original_file_name=original_name, shipment_date=shipment_date, actor=actor,
                        main_customer_id=main_customer_id, route_line=route_line)
                except WorkLimit:
                    raise
                except (ValueError, OSError, InvalidFileException, BadZipFile, PyPdfError, XLRDError) as exc:
                    raise HTTPException(422, "The order file could not be parsed. Check its format, date and customer.") from exc
                raw_file = db.get(RawOrderFile, res["raw_file_id"])
                raw_file.storage_url = persist_source(db, content)
                raw_file.file_sha256, raw_file.file_size_bytes = content.sha256, content.size
                db.flush()
                return UniversalUploadResponse(templates=res.get("templates", []), message=f"Successfully ingested {res['file_name']} via {res['channel']} channel",
                    raw_file_id=res["raw_file_id"], file_name=res["file_name"], channel=res["channel"],
                    total_sub_customers_detected=res["detected_sub_customers"], created_orders_count=len(res["created_orders"]),
                    total_items_processed=res["total_items_processed"], matched_items_count=res["matched_items_count"],
                    unmatched_items_count=res["unmatched_items_count"], match_rate_percentage=res["match_rate_percentage"],
                    total_net_weight_kg=res["total_net_weight_kg"], procurement_consolidated_skus=res["procurement_consolidated_skus"]).model_dump(mode="json")
            return run_once(db, actor_id=actor.id, operation="orders.upload_universal.v1", key=request_key,
                payload={"filename": original_name, "sha256": content.sha256, "size": content.size,
                    "shipment_date": shipment_date, "route_line": route_line, "main_customer_id": main_customer_id}, execute=apply)
    except WorkLimit:
        raise
    except UploadTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "The uploaded file is empty or has an invalid name") from exc


@router.get("/upload-results/{request_key}")
def upload_result(request_key: str, channel: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL))):
    validate_request_key(request_key)
    if channel not in {"matrix", "universal"}:
        raise HTTPException(422, "Choose matrix or universal upload recovery")
    receipt = db.query(RequestResult).filter_by(actor_id=actor.id,
        operation=f"orders.upload_{channel}.v1", request_key=request_key).first()
    if not receipt or receipt.response_json is None:
        raise HTTPException(404, "No completed upload result is available yet. Retry the original file and details.")
    return receipt.response_json


@router.get("/{raw_file_id}/source")
def download_order_source(raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL))):
    raw = db.get(RawOrderFile, raw_file_id)
    if not raw:
        raise HTTPException(404, "Order file not found")
    with source_path(raw) as path:
        data = path.read_bytes()
    from urllib.parse import quote
    name = safe_filename(raw.file_name)
    return Response(data, media_type=MIME[Path(name).suffix.lower()],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class QuantityCorrection(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)
    quantity: float = Field(ge=0, le=100000000, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=16)
    pack_size: str | None = Field(default=None, max_length=64)
    net_weight_kg: float | None = Field(default=None, gt=0, le=100000000, allow_inf_nan=False)
    note: str = Field(min_length=1, max_length=512)


class QuantityUnitChoice(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)
    unit: str = Field(min_length=1, max_length=16)


class BulkQuantityCorrection(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)
    row_ids: list[str] = Field(min_length=1, max_length=200)
    unit: str | None = Field(default=None, min_length=1, max_length=16)
    pack_size: str | None = Field(default=None, min_length=1, max_length=64)
    note: str = Field(min_length=1, max_length=512)
    preview_only: bool = False


def pending_quantity_file(db, identity, *, lock=True):
    raw = (
        bounded_raw(db, identity, lock=lock)
    )
    if not raw or raw.status not in {OrderStatus.UPLOADED, OrderStatus.PARSED}:
        raise HTTPException(409, "Quantity review is only available before orders are confirmed")
    return raw


def quantity_response(db, raw, offset=0, limit=50, *, issues_only=False):
    drafts = order_drafts(raw)
    rows = quantity_rows(db, raw, drafts)
    return quantity_review_data(drafts, rows, offset=offset, limit=limit, issues_only=issues_only)


@router.get("/{raw_file_id}/quantity-review")
def get_quantity_review(
    raw_file_id: str,
    offset: int = 0,
    limit: int = 50,
    issues_only: bool = False,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    raw = pending_quantity_file(db, raw_file_id, lock=False)
    return quantity_response(db, raw, max(0, offset), max(1, min(100, limit)), issues_only=issues_only)


def save_quantity_drafts(db, raw, drafts, actor, before, event):
    raw.acknowledgment_summary = {
        **(raw.acknowledgment_summary or {}),
        "drafts": [d.model_dump(mode="json") for d in drafts],
        "validation": {"can_split": False},
    }
    db.add(
        AuditLog(
            event_type=event,
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="raw_order_files",
            target_id=raw.id,
            before_state=before,
            after_state={"revision": draft_revision(drafts)},
        )
    )
    db.flush()


@router.put("/{raw_file_id}/quantity-review/{row_id}")
def correct_order_quantity(
    raw_file_id: str,
    row_id: str,
    payload: QuantityCorrection,
    issues_only: bool = False,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    raw = pending_quantity_file(db, raw_file_id)
    drafts = order_drafts(raw)
    if draft_revision(drafts) != payload.expected_revision:
        raise HTTPException(409, "Order quantities changed. Reload the review before saving")
    try:
        d_index, index = map(int, row_id.split(":"))
        if d_index < 0 or index < 0:
            raise ValueError()
        item = drafts[d_index].items[index]
        label = pack_key(payload.pack_size or "") if payload.quantity > 0 else item.pack_size
    except (ValueError, IndexError):
        raise HTTPException(
            422, "Select an existing row and an explicit pack size such as 100g"
        ) from None
    if payload.quantity > 0 and unit_key(payload.unit) not in {"kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"}:
        raise HTTPException(422, "Select a supported quantity unit")
    if not payload.note.strip():
        raise HTTPException(422, "Describe the quantity correction")
    before = {"row_id": row_id, "item": item.model_dump(mode="json")}
    item.source = {
        **item.source,
        "original": item.source.get("original", before["item"]),
        "reviewed_by": actor.id,
        "review_note": payload.note.strip(),
    }
    item.quantity = payload.quantity
    if payload.quantity == 0:
        item.action = ActionType.CANCEL
    item.unit = unit_key(payload.unit)
    item.pack_size = label
    item.net_weight_kg = payload.net_weight_kg
    item.quantity_issues = []
    save_quantity_drafts(db, raw, drafts, actor, before, "ORDER_QUANTITY_CORRECTED")
    rows = quantity_rows(db, raw, drafts)
    return {**quantity_review_data(drafts, rows, issues_only=issues_only),
            "changed_rows": [row for row in rows if row["row_id"] == row_id]}


@router.post("/{raw_file_id}/quantity-review/bulk")
def correct_order_quantities(
    raw_file_id: str,
    payload: BulkQuantityCorrection,
    issues_only: bool = True,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    raw = pending_quantity_file(db, raw_file_id, lock=not payload.preview_only)
    drafts = order_drafts(raw)
    if draft_revision(drafts) != payload.expected_revision:
        raise HTTPException(409, "Order quantities changed. Reload the review before saving")
    if len(set(payload.row_ids)) != len(payload.row_ids):
        raise HTTPException(422, "Select each order line once")
    if not payload.note.strip() or (payload.unit is None and payload.pack_size is None):
        raise HTTPException(422, "Choose a unit or pack size and describe the correction")
    unit = unit_key(payload.unit) if payload.unit else None
    if unit is not None and unit not in {"kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"}:
        raise HTTPException(422, "Select a supported quantity unit")
    try:
        size = pack_key(payload.pack_size) if payload.pack_size is not None else None
    except ValueError:
        raise HTTPException(422, "Enter an explicit physical pack size such as 100g") from None
    before = []
    for row_id in payload.row_ids:
        try:
            d_index, index = map(int, row_id.split(":"))
            if d_index < 0 or index < 0 or row_id != f"{d_index}:{index}":
                raise ValueError()
            item = drafts[d_index].items[index]
            if item.action == ActionType.CANCEL or item.quantity <= 0:
                raise ValueError()
        except (ValueError, IndexError):
            raise HTTPException(422, f"Select an existing ordered line: {row_id}") from None
        original = item.model_dump(mode="json")
        before.append({"row_id": row_id, "item": original})
        item.source = {**item.source, "original": item.source.get("original", original),
                       "reviewed_by": actor.id, "review_note": payload.note.strip()}
        if unit is not None:
            item.unit = unit
        if size is not None:
            item.pack_size = size
        item.quantity_issues = []
    rows = quantity_rows(db, raw, drafts)
    selected_ids = set(payload.row_ids)
    changed = [row for row in rows if row["row_id"] in selected_ids]
    invalid = [row for row in changed if row["issue"]]
    if invalid:
        row = invalid[0]
        raise HTTPException(422, f"No changes saved. {row['client_code']} / {row['product_name']}: {row['issue']}")
    if payload.preview_only:
        return {"revision": payload.expected_revision, "items": changed, "changed_count": len(changed)}
    save_quantity_drafts(db, raw, drafts, actor, {"rows": before}, "ORDER_QUANTITIES_BULK_CORRECTED")
    return {**quantity_review_data(drafts, rows, issues_only=issues_only),
            "changed_rows": changed, "changed_count": len(changed)}


@router.post("/{raw_file_id}/quantity-unit")
def choose_unknown_quantity_unit(
    raw_file_id: str,
    payload: QuantityUnitChoice,
    issues_only: bool = False,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    raw = pending_quantity_file(db, raw_file_id)
    drafts = order_drafts(raw)
    if draft_revision(drafts) != payload.expected_revision:
        raise HTTPException(409, "Order quantities changed. Reload before choosing a unit")
    unit = unit_key(payload.unit)
    if unit not in {"kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"}:
        raise HTTPException(422, "Select a supported quantity unit")
    changed = []
    for draft in drafts:
        for item in draft.items:
            if unit_key(item.unit) in {"kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"}:
                continue
            item.source = {
                **item.source,
                "raw_unit": item.source.get("raw_unit", item.unit),
                "unit_reviewed_by": actor.id,
            }
            item.unit = unit
            changed.append(item.raw_text)
    save_quantity_drafts(
        db,
        raw,
        drafts,
        actor,
        {"changed_rows": len(changed), "selected_unit": unit},
        "ORDER_QUANTITY_UNIT_SELECTED",
    )
    return quantity_response(db, raw, issues_only=issues_only)


@router.get("/{raw_file_id}/validate")
def validate_order_matrix(
    raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    try:
        result = OrderValidationService(db).validate(raw_file_id=raw_file_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "raw_file_id": result.raw_file_id,
        "status": OrderStatus.PARSED.value,
        "sheets": result.sheets,
        "total_customers": result.total_customers,
        "total_product_rows": result.total_product_rows,
        "resolved_product_rows": result.resolved_product_rows,
        "unresolved": [issue.__dict__ for issue in result.unresolved],
        "can_split": result.can_split,
        "quantity_issue_count": len(result.quantity_issues),
        "quantity_review": result.quantity_review,
    }


@router.post("/{raw_file_id}/split", response_model=OrderSplitResponse)
def split_order_matrix(
    raw_file_id: str,
    main_customer_id: str | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)),
):
    """
    Splits the acknowledged order matrix into individual sub-customer orders,
    enforcing SKU standardization, deduplicating identical items, and linking to the parent main customer.
    Auto-validates if pending validation.
    """
    raw_file = bounded_raw(db, raw_file_id, lock=True)
    if not raw_file:
        raise HTTPException(status_code=404, detail="Raw order file not found")

    validation = (raw_file.acknowledgment_summary or {}).get("validation", {})
    if raw_file.status == OrderStatus.UPLOADED:
        try:
            val_res = OrderValidationService(db).validate(raw_file_id=raw_file.id, actor=actor)
            if not val_res.can_split:
                raise HTTPException(
                    status_code=409,
                    detail=f"Resolve {len(val_res.unresolved)} unmatched products and {len(val_res.quantity_issues)} quantity issues before splitting.",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif raw_file.status != OrderStatus.PARSED or not validation.get("can_split", False):
        raise HTTPException(
            status_code=409,
            detail="Matrix must be validated and all unresolved SKUs mapped before splitting",
        )

    drafts = order_drafts(raw_file)
    quantity_issues = [row for row in quantity_rows(db, raw_file, drafts) if row["issue"]]
    if quantity_issues:
        raise HTTPException(
            409, f"Resolve {len(quantity_issues)} quantity issues in intake review before splitting"
        )

    if main_customer_id:
        raw_file.main_customer_id = main_customer_id
    try:
        client_orders = OrderIntake(db).materialize(raw_file=raw_file, drafts=drafts).orders
        WorkflowService(db).transition(
            target=raw_file,
            to_status=OrderStatus.SPLIT_AND_VALIDATED,
            actor=actor,
            target_table="raw_order_files",
        )
        batch = PurchasingBatchService(db).consolidate_raw_file(raw_file.id, actor=actor)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.flush()

    total_items = (
        db.query(ClientOrderItem)
        .join(ClientOrder)
        .filter(ClientOrder.raw_order_file_id == raw_file.id)
        .count()
    )
    total_weight = sum(o.total_estimated_net_weight_kg for o in client_orders)

    samples = [
        {
            "order_ref_id": o.order_ref_id,
            "client_code": o.customer.client_code,
            "route_group": o.customer.route_group or "GENERAL",
            "items_count": o.total_ordered_items_count,
            "estimated_net_weight_kg": float(o.total_estimated_net_weight_kg),
        }
        for o in client_orders
    ]

    return OrderSplitResponse(
        raw_file_id=raw_file.id,
        batch_id=batch.id,
        status=raw_file.status.value,
        total_client_orders_created=len(client_orders),
        total_line_items_created=total_items,
        total_estimated_weight_kg=round(float(total_weight), 2),
        sample_orders=samples[:10],
    )


@router.post("/{raw_file_id}/add-adhoc-order")
def add_adhoc_order(
    raw_file_id: str,
    payload: AdHocOrderRequest,
    request_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Adds manually entered order items to the active shipment and designated route group.
    """
    def apply():
        raw_file = bounded_raw(db, raw_file_id)
        if not raw_file:
            raise HTTPException(status_code=404, detail="Raw order file not found")

        client_code = payload.client_code.strip()
        detected_group = payload.route_group
        if not detected_group:
            if "-" in client_code:
                detected_group = f"GROUP {client_code.split('-')[0].strip().upper()}"
            else:
                detected_group = raw_file.route_line

        draft = CanonicalOrderDraft(
            source_file=payload.source_notes or "ad-hoc",
            channel=IngestionChannel.IMAGE_CHAT,
            client_code=client_code,
            route_line=detected_group,
            items=[
                CanonicalDraftItem(
                    raw_text=f"{it.t_code} - {it.name or ''} ({payload.source_notes})",
                    product_code=it.t_code,
                    product_name=it.name or it.t_code,
                    pack_size=it.pack_size,
                    quantity=it.pack_count,
                    unit="pack",
                    net_weight_kg=it.estimated_kg,
                )
                for it in payload.items
            ],
        )
        try:
            client_order = OrderIntake(db).append_adhoc(raw_file=raw_file, draft=draft)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.flush()
        items_added = sum(it.pack_count for it in payload.items)

        return {
            "message": f"Successfully added {items_added} items to {client_code} ({detected_group})",
            "order_ref_id": client_order.order_ref_id,
            "customer_code": client_code,
            "route_group": detected_group,
            "total_items": client_order.total_ordered_items_count,
            "estimated_net_weight_kg": float(client_order.total_estimated_net_weight_kg),
        }

    return run_once(db, actor_id=actor.id, operation="orders.append_adhoc.v1", key=request_key,
        payload={"target_id": raw_file_id, "body": payload.model_dump(mode="json")}, execute=apply)


@router.get("/{raw_file_id}/client-orders")
def get_client_orders(
    raw_file_id: str,
    response: Response,
    page: LegacyPage = Depends(),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Returns all split client orders for a raw order file grouped by route group.
    """
    query = (db.query(ClientOrder).filter(ClientOrder.raw_order_file_id == raw_file_id)
        .options(joinedload(ClientOrder.customer)).order_by(ClientOrder.id))
    orders, total = page.rows(query, response, replacement=f"/orders/files/{raw_file_id}/orders")
    grouped = {}
    for o in orders:
        grp = o.customer.route_group if o.customer and o.customer.route_group else "GENERAL"
        if grp not in grouped:
            grouped[grp] = []
        grouped[grp].append(
            {
                "order_id": o.id,
                "order_ref_id": o.order_ref_id,
                "client_code": o.customer.client_code if o.customer else "Unknown",
                "items_count": o.total_ordered_items_count,
                "estimated_weight_kg": float(o.total_estimated_net_weight_kg or 0.0),
                "status": o.status.value,
            }
        )

    return {"raw_file_id": raw_file_id, "total_orders": total, "groups": grouped}


@router.get("/{raw_file_id}/acknowledgment")
def download_acknowledgment(
    raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    _actor: User = Depends(require_access(UserRole.GENERAL)),
):
    raw = bounded_raw(db, raw_file_id)
    if not raw:
        raise HTTPException(404, "Order file not found")
    if not all(
        "order_id" in row
        for row in (raw.acknowledgment_summary or {}).get("sub_customer_details", [])
    ) or not (raw.acknowledgment_summary or {}).get("sub_customer_details"):
        prepare_acknowledgment(raw)
    return Response(
        acknowledgment_csv(raw).encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="acknowledgment-{raw.id}.csv"'},
    )
