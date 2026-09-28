from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access, require_human_access
from app.models.order import RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem
from app.models.user import User, UserRole
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService
from app.services.purchasing_aggregator import PurchasingAggregator

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


def _to_purchasing_item_out(it) -> PurchasingItemOut:
    tot_packs = it.total_required_pack_count or 0
    buf_pct = float(it.buffer_percentage or 0.0)
    if buf_pct > 0:
        ord_packs = max(1, int(round(tot_packs / (1.0 + buf_pct / 100.0))))
        buf_packs = max(0, tot_packs - ord_packs)
    else:
        ord_packs = tot_packs
        buf_packs = 0

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
    )


class MasterPurchasingResponse(BaseModel):
    batch_id: str
    batch_reference: str
    status: str | None = None
    created_at: str | None = None
    total_skus: int
    total_ordered_weight_kg: float
    total_buffer_weight_kg: float
    total_required_weight_kg: float
    items: list[PurchasingItemOut]


class CombineOrdersRequest(BaseModel):
    raw_file_ids: list[str] = Field(default_factory=list, max_length=500)
    client_order_ids: list[str] | None = None
    batch_reference: str | None = None
    shipment_date: str | None = None
    route_code: str | None = None


class BufferUpdateRequest(BaseModel):
    buffer_percentage: float = Field(ge=0, le=100)
    save_as_default: bool = True


class ProcurementItemConfirmationRequest(BaseModel):
    item_id: str
    purchased_pack_count: int = Field(ge=0)
    purchased_weight_kg: float = Field(ge=0)
    shortage_reason: str | None = None
    replacement_product_id: str | None = None


class ProcurementBatchConfirmationRequest(BaseModel):
    items: list[ProcurementItemConfirmationRequest] = Field(min_length=1, max_length=2000)


@router.put("/batches/{batch_id}/confirm")
def confirm_procurement_batch(
    batch_id: str,
    payload: ProcurementBatchConfirmationRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_human_access(
            UserRole.GENERAL,
            detail="A human procurement role must confirm purchases",
        )
    ),
):
    try:
        batch = ProcurementConfirmationService(db).confirm_batch(
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


@router.get("/latest-batch", response_model=MasterPurchasingResponse)
def get_latest_procurement_batch(db: Session = Depends(get_db)):
    """Fetches the latest master purchasing batch for frontend workstation view."""
    mpl = db.query(MasterPurchasingList).order_by(MasterPurchasingList.created_at.desc()).first()
    if not mpl:
        raise HTTPException(status_code=404, detail="No procurement batches found")

    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).all()
    tot_ordered = sum(float(it.ordered_net_weight_kg or 0.0) for it in items)
    tot_buffer = sum(float(it.buffer_weight_kg or 0.0) for it in items)
    tot_required = sum(float(it.total_required_weight_kg or 0.0) for it in items)

    return MasterPurchasingResponse(
        batch_id=mpl.id,
        batch_reference=mpl.batch_reference,
        status=mpl.status.value if mpl.status else None,
        created_at=mpl.created_at.isoformat() if mpl.created_at else None,
        total_skus=mpl.total_skus_count,
        total_ordered_weight_kg=round(tot_ordered, 2),
        total_buffer_weight_kg=round(tot_buffer, 2),
        total_required_weight_kg=round(tot_required, 2),
        items=[_to_purchasing_item_out(it) for it in items],
    )


@router.get("/batches/{batch_id}", response_model=MasterPurchasingResponse)
def get_procurement_batch(batch_id: str, db: Session = Depends(get_db)):
    """Fetches specific master purchasing batch by ID or reference."""
    mpl = (
        db.query(MasterPurchasingList)
        .filter(
            (MasterPurchasingList.id == batch_id)
            | (MasterPurchasingList.batch_reference == batch_id)
        )
        .first()
    )
    if not mpl:
        raise HTTPException(status_code=404, detail="Procurement batch not found")

    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).all()
    tot_ordered = sum(float(it.ordered_net_weight_kg or 0.0) for it in items)
    tot_buffer = sum(float(it.buffer_weight_kg or 0.0) for it in items)
    tot_required = sum(float(it.total_required_weight_kg or 0.0) for it in items)

    return MasterPurchasingResponse(
        batch_id=mpl.id,
        batch_reference=mpl.batch_reference,
        status=mpl.status.value if mpl.status else None,
        created_at=mpl.created_at.isoformat() if mpl.created_at else None,
        total_skus=mpl.total_skus_count,
        total_ordered_weight_kg=round(tot_ordered, 2),
        total_buffer_weight_kg=round(tot_buffer, 2),
        total_required_weight_kg=round(tot_required, 2),
        items=[_to_purchasing_item_out(it) for it in items],
    )


@router.post("/{raw_file_id}/consolidate", response_model=MasterPurchasingResponse)
def consolidate_purchasing_list(
    raw_file_id: str,
    db: Session = Depends(get_db),
    _actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)
    ),
):
    raw_file = db.query(RawOrderFile).filter(RawOrderFile.id == raw_file_id).first()
    if not raw_file:
        raise HTTPException(status_code=404, detail="Raw order file not found")

    aggregator = PurchasingAggregator(db)
    mpl = aggregator.consolidate_demand(raw_file.id)

    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).all()
    tot_ordered = sum(float(it.ordered_net_weight_kg or 0.0) for it in items)
    tot_buffer = sum(float(it.buffer_weight_kg or 0.0) for it in items)
    tot_required = sum(float(it.total_required_weight_kg or 0.0) for it in items)

    return MasterPurchasingResponse(
        batch_id=mpl.id,
        batch_reference=mpl.batch_reference,
        total_skus=mpl.total_skus_count,
        total_ordered_weight_kg=round(tot_ordered, 2),
        total_buffer_weight_kg=round(tot_buffer, 2),
        total_required_weight_kg=round(tot_required, 2),
        items=[_to_purchasing_item_out(it) for it in items],
    )


@router.post("/combine-and-consolidate", response_model=MasterPurchasingResponse)
def combine_orders_for_procurement(
    payload: CombineOrdersRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_1)
    ),
):
    """
    Master Purchase Generation: Manual merge tool allowing staff to combine
    different main customer orders or order files for streamlined bulk purchasing.
    """
    aggregator = PurchasingAggregator(db)
    try:
        mpl = aggregator.combine_and_consolidate(
            raw_file_ids=payload.raw_file_ids,
            client_order_ids=payload.client_order_ids,
            batch_ref=payload.batch_reference,
            shipment_date=payload.shipment_date,
            route_code=payload.route_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items = db.query(PurchasingItem).filter(PurchasingItem.purchasing_list_id == mpl.id).all()
    tot_ordered = sum(float(it.ordered_net_weight_kg or 0.0) for it in items)
    tot_buffer = sum(float(it.buffer_weight_kg or 0.0) for it in items)
    tot_required = sum(float(it.total_required_weight_kg or 0.0) for it in items)

    return MasterPurchasingResponse(
        batch_id=mpl.id,
        batch_reference=mpl.batch_reference,
        total_skus=mpl.total_skus_count,
        total_ordered_weight_kg=round(tot_ordered, 2),
        total_buffer_weight_kg=round(tot_buffer, 2),
        total_required_weight_kg=round(tot_required, 2),
        items=[_to_purchasing_item_out(it) for it in items],
    )


@router.put("/items/{item_id}/buffer")
def adjust_purchasing_buffer(
    item_id: str,
    payload: BufferUpdateRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(
        require_human_access(UserRole.GENERAL)
    ),
):
    """
    Buffer Addition:
    Staff manually inputs a yield or buffer percentage for products,
    which the system remembers and defaults to for future identical orders.
    """
    item = db.query(PurchasingItem).filter(PurchasingItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="PurchasingItem not found")

    aggregator = PurchasingAggregator(db)
    result = aggregator.update_product_buffer(
        product_id=item.product_id,
        new_buffer_pct=payload.buffer_percentage,
        purchasing_item_id=item.id,
        save_as_default=payload.save_as_default,
    )
    return {"message": "Buffer updated successfully and remembered as default", "details": result}


@router.get("/batches/{batch_id}/buyer-sheet")
def export_talad_thai_buyer_sheet(
    batch_id: str, format: str = "json", db: Session = Depends(get_db)
):
    """
    Talad Thai Market Buyer Sheet:
    Generates a clean procurement sheet sorted by Thai produce names with target weights
    (including sticky trimming buffers) for morning market buyers.
    """
    mpl = (
        db.query(MasterPurchasingList)
        .filter(
            (MasterPurchasingList.id == batch_id)
            | (MasterPurchasingList.batch_reference == batch_id)
        )
        .first()
    )
    if not mpl:
        raise HTTPException(status_code=404, detail="Procurement batch not found")

    items_data = []
    for it in sorted(mpl.items, key=lambda x: (x.product.name_th or x.product.name_vi or "")):
        p = it.product
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
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{item["ordered_weight_kg"]:.2f} kg</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; color: #b45309;">+{item["buffer_pct"]:.0f}% ({item["buffer_weight_kg"]:.2f} kg)</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: right; font-weight: bold; font-size: 1.1em; background-color: #f0fdf4;">{item["target_purchase_weight_kg"]:.2f} kg</td>
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
        "total_required_weight_kg": float(mpl.total_required_weight_kg or 0.0),
        "items": items_data,
    }
