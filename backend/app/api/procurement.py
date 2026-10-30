from decimal import Decimal
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.dependencies import require_access, require_action
from app.core.policy import Action
from app.core.read_models import (
    MAX_DETAIL_LINES,
    LegacyPage,
    bounded_headers,
    purchase_file_links,
    raw_headers,
)
from app.core.registers import RegisterQuery
from app.core.values import INT32_MAX, PURCHASE_WEIGHT_MAX
from app.core.work_limits import WorkLimit
from app.models.master_data import ProduceType, Product
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem
from app.models.user import User, UserRole
from app.modules.procurement.batch import PurchasingBatchService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService

router = APIRouter(
    prefix="/procurement",
    tags=["Stage 01: Master Purchasing List"],
    dependencies=[Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_1))],
)


class PurchasingItemOut(BaseModel):
    item_id: str
    product_id: str
    product_code: str
    product_name: str
    produce_type: str | None = None
    pack_size: str
    ordered_pack_count: int
    ordered_weight_kg: float
    buffer_percentage: float
    buffer_pack_count: int
    buffer_weight_kg: float
    total_purchase_target_packs: int
    total_purchase_target_weight_kg: float
    required_packs: int
    required_weight_kg: float
    confirmed_purchased_pack_count: int | None = None
    confirmed_purchased_weight_kg: float | None = None
    shortage_reason: str | None = None
    replacement_product_id: str | None = None
    replacement_product_code: str | None = None
    replacement_product_name: str | None = None


def _to_purchasing_item_out(it: PurchasingItem, ordered_packs: int) -> PurchasingItemOut:
    tot_packs = it.total_required_pack_count or 0
    buf_pct = float(it.buffer_percentage or 0.0)
    ord_packs = ordered_packs
    buf_packs = max(0, tot_packs - ord_packs)

    ord_wt = float(it.ordered_net_weight_kg or 0.0)
    buf_wt = float(it.buffer_weight_kg or 0.0)
    target_wt = float(it.total_required_weight_kg or (ord_wt + buf_wt))

    produce_type_val = None
    if it.product and getattr(it.product, "produce_type", None):
        produce_type_val = (
            it.product.produce_type.value
            if hasattr(it.product.produce_type, "value")
            else str(it.product.produce_type)
        )

    return PurchasingItemOut(
        item_id=it.id,
        product_id=it.product_id,
        product_code=it.product.t_code if it.product else "",
        product_name=f"{it.product.name_vi} / {it.product.name_en}" if it.product else "",
        produce_type=produce_type_val,
        pack_size=it.pack_size,
        ordered_pack_count=ord_packs,
        ordered_weight_kg=ord_wt,
        buffer_percentage=buf_pct,
        buffer_pack_count=buf_packs,
        buffer_weight_kg=buf_wt,
        total_purchase_target_packs=tot_packs,
        total_purchase_target_weight_kg=target_wt,
        required_packs=tot_packs,
        required_weight_kg=target_wt,
        confirmed_purchased_pack_count=it.confirmed_purchased_pack_count,
        confirmed_purchased_weight_kg=float(it.confirmed_purchased_weight_kg)
        if it.confirmed_purchased_weight_kg is not None
        else None,
        shortage_reason=it.shortage_reason,
        replacement_product_id=it.replacement_product_id,
        replacement_product_code=it.replacement_product.t_code if it.replacement_product else None,
        replacement_product_name=it.replacement_product.name_en if it.replacement_product else None,
    )


class MasterPurchasingResponse(BaseModel):
    batch_id: str
    batch_reference: str
    status: str | None = None
    substitutions_supported: bool = True
    created_at: str | None = None
    total_skus: int
    total_ordered_weight_kg: float
    total_buffer_weight_kg: float
    total_required_weight_kg: float
    items: list[PurchasingItemOut]
    source_files: list[dict] = Field(default_factory=list)


def _batch_response(db: Session, mpl: MasterPurchasingList) -> MasterPurchasingResponse:
    count = db.query(func.count(PurchasingItem.id)).filter(PurchasingItem.purchasing_list_id == mpl.id).scalar() or 0
    if count > MAX_DETAIL_LINES:
        raise WorkLimit("This retained purchase list exceeds its detail budget; arrange a controlled split or review")
    links = purchase_file_links()
    sources = bounded_headers(db.query(RawOrderFile).join(links, links.c.file_id == RawOrderFile.id)
        .filter(links.c.batch_id == mpl.id).options(raw_headers(), joinedload(RawOrderFile.main_customer))
        .order_by(RawOrderFile.file_name, RawOrderFile.id))
    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).options(
        joinedload(PurchasingItem.product), joinedload(PurchasingItem.replacement_product)).all()
    ordered = PurchasingBatchService(db).ordered_packs(mpl)
    return MasterPurchasingResponse(
        batch_id=mpl.id,
        batch_reference=mpl.batch_reference,
        status=mpl.status.value if mpl.status else None,
        created_at=mpl.created_at.isoformat() if mpl.created_at else None,
        total_skus=mpl.total_skus_count,
        source_files=[{"file_id": raw.id, "file_name": raw.file_name,
                       "customer": raw.main_customer.company_name if raw.main_customer else "General",
                       "shipment_date": raw.shipment_target_date, "route": raw.route_line} for raw in sources],
        total_ordered_weight_kg=round(sum(float(it.ordered_net_weight_kg or 0) for it in items), 4),
        total_buffer_weight_kg=round(sum(float(it.buffer_weight_kg or 0) for it in items), 4),
        total_required_weight_kg=round(
            sum(float(it.total_required_weight_kg or 0) for it in items), 4
        ),
        items=[
            _to_purchasing_item_out(it, ordered.get((it.product_id, it.pack_size), 0))
            for it in items
        ],
    )


class CombineOrdersRequest(BaseModel):
    raw_file_ids: list[str] = Field(default_factory=list, max_length=500)
    client_order_ids: list[str] | None = Field(default=None, max_length=500)
    batch_reference: str | None = None
    shipment_date: str | None = None
    route_code: str | None = None


class BufferUpdateRequest(BaseModel):
    buffer_percentage: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False)
    save_as_default: bool = True


class ProcurementItemConfirmationRequest(BaseModel):
    item_id: str
    purchased_pack_count: int = Field(ge=0, le=INT32_MAX, strict=True)
    purchased_weight_kg: Decimal = Field(ge=0, le=PURCHASE_WEIGHT_MAX,
                                        max_digits=12, decimal_places=4, allow_inf_nan=False)
    shortage_reason: str | None = None
    replacement_product_id: str | None = None


class ProcurementBatchConfirmationRequest(BaseModel):
    items: list[ProcurementItemConfirmationRequest] = Field(min_length=1, max_length=2000)


@router.get("/source-orders")
def procurement_source_orders(response: Response, page: LegacyPage = Depends(), db: Session = Depends(get_db, scope="function")):
    query = (db.query(RawOrderFile).options(raw_headers(), selectinload(RawOrderFile.main_customer)).filter(
        RawOrderFile.status == OrderStatus.PURCHASING_CONSOLIDATED,
        RawOrderFile.client_orders.any(ClientOrder.status == OrderStatus.PURCHASING_CONSOLIDATED), ~RawOrderFile.client_orders.any((ClientOrder.status != OrderStatus.PURCHASING_CONSOLIDATED) & (ClientOrder.status != OrderStatus.CANCELLED)))
        .order_by(RawOrderFile.created_at.desc(), RawOrderFile.id))
    files, _ = page.rows(query, response, replacement="/procurement/source-files")
    ids = [raw.id for raw in files]
    stats = {identity: (count, weight) for identity, count, weight in db.query(ClientOrder.raw_order_file_id,
        func.count(ClientOrder.id), func.sum(ClientOrder.total_estimated_net_weight_kg)).filter(
        ClientOrder.raw_order_file_id.in_(ids), ClientOrder.status != OrderStatus.CANCELLED).group_by(ClientOrder.raw_order_file_id)} if ids else {}
    category_rows = db.query(ClientOrder.raw_order_file_id, Product.produce_type).join(ClientOrderItem,
        ClientOrderItem.client_order_id == ClientOrder.id).join(Product, Product.id == ClientOrderItem.product_id).filter(
        ClientOrder.raw_order_file_id.in_(ids), ClientOrder.status != OrderStatus.CANCELLED).distinct().all() if ids else []
    categories = {}
    for identity, category in category_rows:
        categories.setdefault(identity, []).append(category.value)
    return [{"raw_file_id": raw.id, "file_name": raw.file_name,
        "main_customer": raw.main_customer.company_name if raw.main_customer else "General",
        "shipment_date": raw.shipment_target_date, "route_code": raw.route_line,
        "order_count": stats[raw.id][0], "categories": sorted(categories.get(raw.id, [])),
        "weight_kg": round(float(stats[raw.id][1] or 0), 2)} for raw in files]


@router.put("/batches/{batch_id}/confirm")
def confirm_procurement_batch(
    batch_id: str,
    payload: ProcurementBatchConfirmationRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.CONFIRM_PROCUREMENT)
    ),
):
    try:
        batch = ProcurementConfirmationService(db).confirm_and_dispatch(
            batch_id=batch_id,
            confirmations=[
                ProcurementConfirmation(
                    item_id=item.item_id,
                    purchased_pack_count=item.purchased_pack_count,
                    purchased_weight_kg=item.purchased_weight_kg,
                    shortage_reason=item.shortage_reason,
                    replacement_product_id=item.replacement_product_id,
                )
                for item in payload.items
            ],
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "batch_id": batch.id,
        "batch_reference": batch.batch_reference,
        "status": batch.status.value,
        "confirmed_by": batch.confirmed_by_buyer,
        "confirmed_at": batch.confirmed_at,
    }


@router.get("/latest-batch", response_model=MasterPurchasingResponse | None)
def get_latest_procurement_batch(db: Session = Depends(get_db, scope="function")):
    """Fetches actionable work first, falling back to the latest completed batch."""
    mpl = (
        db.query(MasterPurchasingList)
        .filter(
            MasterPurchasingList.status.in_(
                {OrderStatus.PURCHASING_CONSOLIDATED, OrderStatus.PROCUREMENT_CONFIRMED}
            )
        )
        .order_by(MasterPurchasingList.created_at.desc())
        .first()
    )
    if not mpl:
        mpl = db.query(MasterPurchasingList).order_by(MasterPurchasingList.created_at.desc()).first()
    return _batch_response(db, mpl) if mpl else None


@router.get("/batches")
def list_procurement_batches(
    params: RegisterQuery = Depends(),
    db: Session = Depends(get_db, scope="function"),
    view: Literal["pending", "completed", "all"] = "all",
    limit: int = Query(50, ge=1, le=100),
    raw_file_id: str | None = None,
):
    from app.api.registers import procurement_batches
    params.view, params.limit = view, limit
    return procurement_batches(db, params, raw_file_id=raw_file_id)


@router.get("/batches/{batch_id}", response_model=MasterPurchasingResponse)
def get_procurement_batch(batch_id: str, db: Session = Depends(get_db, scope="function")):
    """Fetches specific master purchasing batch by ID or reference."""
    try:
        return _batch_response(db, PurchasingBatchService(db).get(batch_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{raw_file_id}/consolidate", response_model=MasterPurchasingResponse)
def consolidate_purchasing_list(
    raw_file_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)),
):
    try:
        mpl = PurchasingBatchService(db).consolidate_raw_file(raw_file_id, actor=actor)
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _batch_response(db, mpl)


@router.post("/combine-and-consolidate", response_model=MasterPurchasingResponse)
def combine_orders_for_procurement(
    payload: CombineOrdersRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)),
):
    """
    Master Purchase Generation: Manual merge tool allowing staff to combine
    different main customer orders or order files for streamlined bulk purchasing.
    """
    try:
        mpl = PurchasingBatchService(db).consolidate(
            raw_file_ids=payload.raw_file_ids,
            actor=actor,
            client_order_ids=payload.client_order_ids,
            batch_ref=payload.batch_reference,
            shipment_date=payload.shipment_date,
            route_code=payload.route_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _batch_response(db, mpl)


@router.put("/items/{item_id}/buffer")
def adjust_purchasing_buffer(
    item_id: str,
    payload: BufferUpdateRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.EDIT_BUFFERS)),
):
    """Set one line's buffer %; optionally remembered as the product default."""
    try:
        item = PurchasingBatchService(db).set_item_buffer(
            item_id,
            payload.buffer_percentage,
            actor=actor,
            save_as_default=payload.save_as_default,
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "message": "Buffer updated",
        "details": {
            "product_id": item.product_id,
            "default_buffer_percentage": float(item.product.default_buffer_percentage or 0),
            "updated_item": {
                "item_id": item.id,
                "buffer_percentage": float(item.buffer_percentage),
                "buffer_weight_kg": float(item.buffer_weight_kg),
                "total_required_weight_kg": float(item.total_required_weight_kg),
            },
        },
    }


class BatchBufferRequest(BaseModel):
    buffer_percentage: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False)


@router.put("/batches/{batch_id}/buffer", response_model=MasterPurchasingResponse)
def adjust_batch_buffer(
    batch_id: str,
    payload: BatchBufferRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.EDIT_BUFFERS)),
):
    """Apply one buffer % to every line in the batch, atomically."""
    try:
        mpl = PurchasingBatchService(db).set_batch_buffer(
            batch_id, payload.buffer_percentage, actor=actor
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _batch_response(db, mpl)


@router.get("/batches/{batch_id}/buyer-sheet")
def export_talad_thai_buyer_sheet(
    batch_id: str, format: str = "json", category: ProduceType | None = None, db: Session = Depends(get_db, scope="function")
):
    """
    Talad Thai Market Buyer Sheet:
    Generates a clean procurement sheet sorted by Thai produce names with target weights
    (including sticky trimming buffers) for morning market buyers.
    """
    try:
        mpl = PurchasingBatchService(db).get(batch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    items_data = []
    for it in sorted(mpl.items, key=lambda x: (x.product.name_th or x.product.name_vi or "")):
        p = it.product
        if category and p.produce_type != category:
            continue
        items_data.append(
            {
                "t_code": p.t_code if p else "",
                "name_th": p.name_th or "—" if p else "—",
                "name_vi": p.name_vi or "—" if p else "—",
                "name_en": p.name_en or "—" if p else "—",
                "pack_size": it.pack_size,
                "target_packs": it.total_required_pack_count,
                "ordered_weight_kg": float(it.ordered_net_weight_kg),
                "buffer_pct": float(it.buffer_percentage or 0.0),
                "buffer_weight_kg": float(it.buffer_weight_kg or 0.0),
                "target_purchase_weight_kg": float(it.total_required_weight_kg),
            }
        )

    if format == "html":
        rows_html = "".join(
            f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">{escape(str(item["t_code"]))}</td>
            <td style="padding: 8px; border: 1px solid #ddd; font-size: 1.1em; color: #15803d; font-weight: bold;">{escape(str(item["name_th"]))}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{escape(str(item["name_vi"]))} / {escape(str(item["name_en"]))}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{escape(str(item["pack_size"] or ""))}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{item["ordered_weight_kg"]:.4f} kg</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; color: #b45309;">+{item["buffer_pct"]:.2f}% ({item["buffer_weight_kg"]:.4f} kg)</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; font-weight: bold; font-size: 1.1em; background-color: #f0fdf4;">{item["target_purchase_weight_kg"]:.4f} kg</td>
        </tr>
        """
            for item in items_data
        )

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Talad Thai Buyer Procurement Sheet - {escape(mpl.batch_reference)}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 30px; color: #333; }}
                h1 {{ color: #166534; margin-bottom: 5px; }}
                .meta {{ color: #666; margin-bottom: 20px; font-size: 0.9em; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
                th {{ background-color: #166534; color: white; padding: 10px; border: 1px solid #166534; text-align: left; }}
                th.num {{ text-align: right; }}
                @media print {{
                    button {{ display: none; }}
                }}
            </style>
        </head>
        <body>
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <h1>ใบสั่งซื้อตลาดไท (Talad Thai Market Procurement Sheet)</h1>
                    <div class="meta">
                        <strong>Batch Ref:</strong> {escape(mpl.batch_reference)} | 
                        <strong>Date:</strong> {escape(str(mpl.shipment_date or "Current"))} | 
                        <strong>Route:</strong> {escape(mpl.route_code or "ALL")} | 
                        <strong>Total Produce SKUs:</strong> {len(items_data)}
                    </div>
                </div>
                <button onclick="window.print()" style="padding: 10px 18px; background: #166534; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold;">
                    🖨️ Print Buyer Sheet
                </button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Code</th>
                        <th>ชื่อไทย (Thai Produce Name)</th>
                        <th>Tên Tiếng Việt / English</th>
                        <th>Packing</th>
                        <th class="num">Net Ordered</th>
                        <th class="num">Buffer (+%)</th>
                        <th class="num">Target Weight to Buy (นน. ที่ต้องซื้อ)</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    return {
        "batch_reference": mpl.batch_reference,
        "shipment_date": mpl.shipment_date,
        "route_code": mpl.route_code,
        "total_skus": len(items_data),
        "total_required_weight_kg": round(
            sum(item["target_purchase_weight_kg"] for item in items_data), 4
        ),
        "items": items_data,
    }
