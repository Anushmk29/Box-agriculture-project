import os
import uuid
from pathlib import Path
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access
from app.models.master_data import Customer
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.modules.orders.validation import OrderValidationService
from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator
from app.services.matrix_parser import MatrixOrderParser
from app.services.order_splitter import OrderSplitter

router = APIRouter(prefix="/orders", tags=["Stage 01: Order Intake & Splitting"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}
ALLOWED_UNIVERSAL_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".pdf", ".png", ".jpg", ".jpeg"}


class UniversalUploadResponse(BaseModel):
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
    status: str
    total_client_orders_created: int
    total_line_items_created: int
    total_estimated_weight_kg: float
    sample_orders: list[dict]


class AdHocItemInput(BaseModel):
    t_code: str = Field(min_length=1, max_length=64)
    name: str | None = None
    pack_size: str = Field(default="100g", min_length=1, max_length=64)
    pack_count: int = Field(default=1, gt=0)
    estimated_kg: float | None = Field(default=None, gt=0)


class AdHocOrderRequest(BaseModel):
    client_code: str = Field(min_length=1, max_length=64)
    route_group: str | None = None
    source_notes: str | None = "WhatsApp Chat"
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
    route_line: str = Form(...),
    main_customer_id: str | None = Form(None),
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Intake & Acknowledgment:
    Main customer uploads an order matrix. The system immediately discovers and returns
    the total sub-customer count and sheet breakdown for instant human verification.
    """
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
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

    upload_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads"
    )
    os.makedirs(upload_dir, exist_ok=True)

    file_path = os.path.join(upload_dir, f"{uuid.uuid4()}_{original_name}")
    total_bytes = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Workbook exceeds the 25 MB upload limit",
                    )
                buffer.write(chunk)

        parser = MatrixOrderParser(file_path)
        ack_info = parser.inspect_sub_customers()
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except (InvalidFileException, BadZipFile, ValueError, OSError) as exc:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=422,
            detail="The uploaded Excel workbook is invalid or cannot be parsed",
        ) from exc

    # Fast inspection to acknowledge sub-customers immediately
    raw_file = RawOrderFile(
        file_name=original_name,
        storage_url=file_path,
        shipment_target_date=shipment_date,
        route_line=route_line,
        uploaded_by=actor.id,
        main_customer_id=main_customer_id,
        detected_sub_customer_count=ack_info["total_sub_customers"],
        acknowledgment_summary=ack_info,
        status=OrderStatus.UPLOADED,
    )
    db.add(raw_file)
    db.commit()
    db.refresh(raw_file)

    return OrderAcknowledgmentResponse(
        message="Order matrix uploaded and acknowledged successfully.",
        raw_file_id=raw_file.id,
        file_name=raw_file.file_name,
        main_customer_id=raw_file.main_customer_id,
        total_sub_customers_detected=ack_info["total_sub_customers"],
        sub_customer_codes=ack_info["sub_customer_codes"],
        sheets_detected=ack_info["sheets_processed"],
        group_summary=ack_info.get("group_summary", {}),
        sub_customer_details=ack_info.get("sub_customer_details", []),
        status=raw_file.status.value,
        verification_notice=f"Found {ack_info['total_sub_customers']} sub-customers across {len(ack_info['sheets_processed'])} sheets. Please verify before confirming split.",
    )


@router.post("/upload-universal", response_model=UniversalUploadResponse)
def upload_order_file_universal(
    file: UploadFile = File(...),
    shipment_date: str = Form(...),
    route_line: str = Form("GENERAL"),
    main_customer_id: str | None = Form(None),
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Universal Multi-Modal Intake:
    Ingests orders in any format: Excel (.xlsx, .xls), PDF purchase orders, or WhatsApp chat screenshots (.png, .jpg).
    Directly extracts sub-customers, applies standard template weights and units, and generates atomic orders.
    """
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_UNIVERSAL_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{suffix}'. Supported: .xlsx, .xls, .pdf, .png, .jpg, .jpeg",
        )

    upload_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads"
    )
    os.makedirs(upload_dir, exist_ok=True)

    file_path = os.path.join(upload_dir, f"{uuid.uuid4()}_{original_name}")
    total_bytes = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="File exceeds the 25 MB upload limit",
                    )
                buffer.write(chunk)

        orchestrator = AdaptiveOrderOrchestrator(db)
        res = orchestrator.ingest_file(
            file_path=file_path,
            shipment_date=shipment_date,
            uploaded_by_id=actor.id,
            main_customer_id=main_customer_id,
            route_line=route_line,
        )
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except Exception as exc:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=422,
            detail=f"Failed to process {original_name}: {str(exc)}",
        ) from exc

    return UniversalUploadResponse(
        message=f"Successfully ingested {res['file_name']} via {res['channel']} channel",
        raw_file_id=res["raw_file_id"],
        file_name=res["file_name"],
        channel=res["channel"],
        total_sub_customers_detected=res["detected_sub_customers"],
        created_orders_count=len(res["created_orders"]),
        total_items_processed=res["total_items_processed"],
        matched_items_count=res["matched_items_count"],
        unmatched_items_count=res["unmatched_items_count"],
        match_rate_percentage=res["match_rate_percentage"],
        total_net_weight_kg=res["total_net_weight_kg"],
        procurement_consolidated_skus=res["procurement_consolidated_skus"],
    )


@router.get("/{raw_file_id}/validate")
def validate_order_matrix(
    raw_file_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)
    ),
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
    }


@router.post("/{raw_file_id}/split", response_model=OrderSplitResponse)
def split_order_matrix(
    raw_file_id: str,
    main_customer_id: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)
    ),
):
    """
    Splits the acknowledged order matrix into individual sub-customer orders,
    enforcing SKU standardization, deduplicating identical items, and linking to the parent main customer.
    Auto-validates if pending validation.
    """
    raw_file = db.query(RawOrderFile).filter(RawOrderFile.id == raw_file_id).first()
    if not raw_file:
        raise HTTPException(status_code=404, detail="Raw order file not found")

    validation = (raw_file.acknowledgment_summary or {}).get("validation", {})
    if raw_file.status == OrderStatus.UPLOADED:
        try:
            val_res = OrderValidationService(db).validate(raw_file_id=raw_file.id, actor=actor)
            if not val_res.can_split:
                raise HTTPException(
                    status_code=409,
                    detail=f"Matrix has {len(val_res.unresolved)} unresolved SKUs. Map them before splitting.",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif raw_file.status != OrderStatus.PARSED or not validation.get("can_split", True):
        raise HTTPException(
            status_code=409,
            detail="Matrix must be validated and all unresolved SKUs mapped before splitting",
        )

    parser = MatrixOrderParser(raw_file.storage_url)
    parsed_data = parser.parse_all_clients()

    splitter = OrderSplitter(db)
    client_orders = splitter.split_raw_order(
        raw_file_id=raw_file.id,
        parsed_data=parsed_data,
        main_customer_id=main_customer_id or raw_file.main_customer_id,
        actor=actor,
    )

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
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Ingests ad-hoc chat/WhatsApp order additions (e.g. BE-DUYÊN herbs from screenshot 1.1/1.2)
    and attaches them directly to the active shipment and designated route group.
    """
    from app.models.master_data import Customer
    from app.services.name_matcher import NameFirstMatcher
    from app.services.order_splitter import parse_pack_size_weight

    raw_file = db.query(RawOrderFile).filter(RawOrderFile.id == raw_file_id).first()
    if not raw_file:
        raise HTTPException(status_code=404, detail="Raw order file not found")

    client_code = payload.client_code.strip()
    detected_group = payload.route_group
    if not detected_group:
        if "-" in client_code:
            prefix = client_code.split("-")[0].strip().upper()
            detected_group = f"GROUP {prefix}"
        else:
            detected_group = raw_file.route_line

    customer = db.query(Customer).filter(Customer.client_code == client_code).first()
    if not customer:
        customer = Customer(
            client_code=client_code,
            company_name=f"Sub-Customer {client_code}",
            billing_email=f"{client_code.lower().replace(' ', '_')}@clienttrade.eu",
            route_group=detected_group,
            destination_country="Norway" if "NGA" in (detected_group or "").upper() else "France",
            parent_customer_id=raw_file.main_customer_id,
            is_main_customer=False,
        )
        db.add(customer)
        db.flush()
    else:
        if detected_group:
            customer.route_group = detected_group

    order_ref = f"ORD-{raw_file.shipment_target_date.replace('-', '')}-{client_code.replace(' ', '_')}-ADHOC"
    client_order = db.query(ClientOrder).filter(ClientOrder.order_ref_id == order_ref).first()
    if not client_order:
        client_order = ClientOrder(
            raw_order_file_id=raw_file.id,
            customer_id=customer.id,
            main_customer_id=raw_file.main_customer_id,
            order_ref_id=order_ref,
            status=OrderStatus.SPLIT_AND_VALIDATED,
        )
        db.add(client_order)
        db.flush()

    matcher = NameFirstMatcher(db)
    items_added = 0
    total_added_weight = 0.0

    for it in payload.items:
        prod, _, _ = matcher.resolve_product(
            raw_name=it.name or it.t_code,
            raw_code=it.t_code,
            main_customer_id=raw_file.main_customer_id,
        )
        if not prod:
            raise HTTPException(
                status_code=400, detail=f"Unresolved produce SKU: {it.t_code} {it.name}"
            )

        pack_weight = parse_pack_size_weight(it.pack_size)
        est_kg = it.estimated_kg or round(it.pack_count * pack_weight, 2)
        raw_text = f"{it.t_code} - {it.name or prod.name_vi} ({payload.source_notes})".strip()

        order_item = ClientOrderItem(
            client_order_id=client_order.id,
            product_id=prod.id,
            raw_produce_text=raw_text,
            pack_size=it.pack_size,
            ordered_pack_count=it.pack_count,
            unit_price_eur=1.50,
            estimated_net_weight_kg=est_kg,
        )
        db.add(order_item)
        items_added += it.pack_count
        total_added_weight += est_kg

    client_order.total_ordered_items_count = (
        client_order.total_ordered_items_count or 0
    ) + items_added
    client_order.total_estimated_net_weight_kg = round(
        float(client_order.total_estimated_net_weight_kg or 0.0) + total_added_weight, 2
    )
    db.commit()

    return {
        "message": f"Successfully added {items_added} items to {client_code} ({detected_group})",
        "order_ref_id": client_order.order_ref_id,
        "customer_code": client_code,
        "route_group": detected_group,
        "total_items": client_order.total_ordered_items_count,
        "estimated_net_weight_kg": float(client_order.total_estimated_net_weight_kg),
    }


@router.get("/{raw_file_id}/client-orders")
def get_client_orders(
    raw_file_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    """
    Returns all split client orders for a raw order file grouped by route group.
    """
    orders = db.query(ClientOrder).filter(ClientOrder.raw_order_file_id == raw_file_id).all()
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

    return {"raw_file_id": raw_file_id, "total_orders": len(orders), "groups": grouped}
