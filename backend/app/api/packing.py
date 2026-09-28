from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access
from app.models.master_data import Customer
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.packing import Box
from app.models.user import User, UserRole
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.services.packing_override_service import PackingOverrideService

router = APIRouter(
    prefix="/packing",
    tags=["Stage 02: Physical Packing & Box Management"],
    dependencies=[Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_2))],
)


class TransferBoxContentsRequest(BaseModel):
    source_box_id: str
    target_box_id: str
    item_ids: list[str] | None = None
    remove_empty_source: bool = False


class BoxOverrideRequest(BaseModel):
    new_catalogue_id: str | None = None
    custom_length_cm: float | None = None
    custom_width_cm: float | None = None
    custom_height_cm: float | None = None
    override_reason: str | None = None


class AddBoxRequest(BaseModel):
    box_catalogue_id: str
    shipment_date: str
    route_code: str
    is_mixed_box: bool = True


class MoveItemsRequest(BaseModel):
    source_box_id: str
    target_box_id: str
    product_id: str
    pack_size: str
    quantity: int = Field(gt=0)
    net_weight_kg: float = Field(gt=0)


class PackingDesignRequest(BaseModel):
    order_id: str


class BoxWeightRequest(BaseModel):
    actual_net_weight_kg: float = Field(ge=0)
    actual_gross_weight_kg: float = Field(ge=0)
    capture_source: str = "MANUAL"


@router.post("/solve-design")
def solve_packing_design(
    payload: PackingDesignRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_2)),
):
    try:
        boxes = PackingPlanningService(db).design_order(order_id=payload.order_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "order_id": payload.order_id,
        "status": OrderStatus.PACKING_DESIGNED.value,
        "boxes_created": len(boxes),
        "box_ids": [box.id for box in boxes],
    }


@router.post("/orders/{client_order_id}/start-floor-packing")
def start_floor_packing(
    client_order_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    try:
        order = PackingPlanningService(db).start_floor_packing(
            order_id=client_order_id, actor=actor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order_id": order.id, "status": order.status.value}


@router.post("/orders/{client_order_id}/confirm-final-pack")
def confirm_final_pack(
    client_order_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_2)
    ),
):
    try:
        boxes = FinalPackingService(db).lock_order(order_id=client_order_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "order_id": client_order_id,
        "status": OrderStatus.FINAL_PACK_LOCKED.value,
        "locked_box_ids": [box.id for box in boxes],
    }


@router.patch("/boxes/{box_id}/weights")
def record_box_weights(
    box_id: str,
    payload: BoxWeightRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    try:
        box = PackingPlanningService(db).record_box_weights(
            box_id=box_id,
            actual_net_weight_kg=payload.actual_net_weight_kg,
            actual_gross_weight_kg=payload.actual_gross_weight_kg,
            capture_source=payload.capture_source,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "box_id": box.id,
        "actual_net_weight_kg": float(box.actual_net_weight_kg),
        "actual_gross_weight_kg": float(box.actual_gross_weight_kg),
        "ready_for_lock": True,
    }


class BoxItemOut(BaseModel):
    product_id: str
    product_code: str
    produce_name: str
    pack_size: str
    packed_count: int
    net_weight_kg: float
    adjustment_type: str


class BoxDetailOut(BaseModel):
    box_id: str
    box_number: int
    box_type_name: str
    is_overridden: bool
    actual_gross_weight_kg: float | None
    actual_net_weight_kg: float | None
    volumetric_weight_kg: float | None
    chargeable_weight_kg: float | None
    is_locked: bool
    qr_code_uid: str
    sticker_label: str
    items: list[BoxItemOut]


class SubCustomerPackingListOut(BaseModel):
    client_order_id: str
    order_ref_id: str
    sub_customer_code: str
    route_code: str
    shipment_date: str
    total_boxes_count: int
    boxes: list[BoxDetailOut]


@router.get("/scan/{qr_code_uid}")
def scan_box(qr_code_uid: str, db: Session = Depends(get_db)):
    box = db.query(Box).filter(Box.qr_code_uid == qr_code_uid).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box QR code was not found")
    order = box.order
    return {
        "box_id": box.id,
        "qr_code_uid": box.qr_code_uid,
        "box_number": box.box_number,
        "order_id": order.id,
        "order_ref_id": order.order_ref_id,
        "customer_code": order.customer.client_code if order.customer else "",
        "route_code": box.route_code,
        "shipment_date": box.shipment_date,
        "is_locked": box.is_locked,
        "actual_net_weight_kg": (
            float(box.actual_net_weight_kg) if box.actual_net_weight_kg is not None else None
        ),
        "actual_gross_weight_kg": (
            float(box.actual_gross_weight_kg) if box.actual_gross_weight_kg is not None else None
        ),
        "items": [
            {
                "product_code": item.product.t_code if item.product else "",
                "produce_name": item.product.name_en if item.product else "",
                "pack_size": item.pack_size,
                "packed_count": item.final_packed_count,
                "net_weight_kg": float(item.final_net_weight_kg),
            }
            for item in box.items
        ],
    }


@router.get("/orders/{client_order_id}/packing-list", response_model=SubCustomerPackingListOut)
def get_sub_customer_packing_list(client_order_id: str, db: Session = Depends(get_db)):
    """
    Generates a specific packing list outlining items and box numbers per sub-customer.
    """
    order = db.query(ClientOrder).filter(ClientOrder.id == client_order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="ClientOrder not found")

    boxes = (
        db.query(Box).filter(Box.client_order_id == client_order_id).order_by(Box.box_number).all()
    )

    main_code = "TT"
    if order.main_customer:
        mc = order.main_customer.client_code
        main_code = "TT" if ("TONIO" in mc or "TETE" in mc) else mc.split("-")[0]
    elif order.raw_file and order.raw_file.route_line:
        rl = order.raw_file.route_line
        main_code = "TT" if rl == "TETE" else rl

    sub_code = order.customer.client_code if order.customer else "SUB"
    total_boxes = len(boxes) if boxes else 1

    box_list = []
    for b in boxes:
        type_name = (
            b.override_box_type.size_name
            if b.override_box_type
            else (b.box_type.size_name if b.box_type else "Custom")
        )
        sticker = f"{main_code}-{sub_code} / {b.box_number} of {total_boxes}"
        item_list = [
            BoxItemOut(
                product_id=it.product_id,
                product_code=it.product.t_code if it.product else "",
                produce_name=f"{it.product.name_vi} / {it.product.name_en}" if it.product else "",
                pack_size=it.pack_size,
                packed_count=it.final_packed_count,
                net_weight_kg=float(it.final_net_weight_kg),
                adjustment_type=it.adjustment_type.value,
            )
            for it in b.items
        ]
        box_list.append(
            BoxDetailOut(
                box_id=b.id,
                box_number=b.box_number,
                box_type_name=type_name,
                is_overridden=b.is_manually_overridden,
                actual_gross_weight_kg=float(b.actual_gross_weight_kg)
                if b.actual_gross_weight_kg is not None
                else None,
                actual_net_weight_kg=float(b.actual_net_weight_kg)
                if b.actual_net_weight_kg is not None
                else None,
                volumetric_weight_kg=float(b.volumetric_weight_kg)
                if b.volumetric_weight_kg is not None
                else None,
                chargeable_weight_kg=float(b.chargeable_weight_kg)
                if b.chargeable_weight_kg is not None
                else None,
                is_locked=b.is_locked,
                qr_code_uid=b.qr_code_uid,
                sticker_label=sticker,
                items=item_list,
            )
        )

    return SubCustomerPackingListOut(
        client_order_id=order.id,
        order_ref_id=order.order_ref_id,
        sub_customer_code=order.customer.client_code if order.customer else "",
        route_code=(
            order.customer.route_group
            if order.customer and order.customer.route_group
            else order.raw_file.route_line
            if order.raw_file
            else "TETE"
        ),
        shipment_date=order.raw_file.shipment_target_date,
        total_boxes_count=len(boxes),
        boxes=box_list,
    )


@router.put("/boxes/{box_id}/override-size")
def override_box_size(
    box_id: str,
    payload: BoxOverrideRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Box Management: Floor staff manual override to adjust physical box dimensions or catalogue type
    if inventory differs from the initial plan.
    """
    service = PackingOverrideService(db)
    try:
        updated_box = service.override_box_size(
            box_id=box_id,
            new_catalogue_id=payload.new_catalogue_id,
            custom_length_cm=payload.custom_length_cm,
            custom_width_cm=payload.custom_width_cm,
            custom_height_cm=payload.custom_height_cm,
            override_reason=payload.override_reason,
        )
        return {
            "message": "Box size overridden successfully",
            "box_id": updated_box.id,
            "box_number": updated_box.box_number,
            "volumetric_weight_kg": float(updated_box.volumetric_weight_kg or 0.0),
            "chargeable_weight_kg": float(updated_box.chargeable_weight_kg or 0.0),
            "is_overridden": updated_box.is_manually_overridden,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{client_order_id}/add-box")
def add_extra_box_to_order(
    client_order_id: str,
    payload: AddBoxRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Box Management: Allows packers to add an extra box, adjusting total box count.
    """
    service = PackingOverrideService(db)
    try:
        new_box = service.add_extra_box_to_order(
            client_order_id=client_order_id,
            box_catalogue_id=payload.box_catalogue_id,
            shipment_date=payload.shipment_date,
            route_code=payload.route_code,
            is_mixed_box=payload.is_mixed_box,
        )
        return {
            "message": "Extra box added successfully",
            "box_id": new_box.id,
            "box_number": new_box.box_number,
            "qr_code_uid": new_box.qr_code_uid,
            "volumetric_weight_kg": float(new_box.volumetric_weight_kg or 0.0),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/boxes/move-items")
def move_items_between_boxes(
    payload: MoveItemsRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """Allows packers to reallocate produce items between boxes on the floor."""
    service = PackingOverrideService(db)
    try:
        result = service.move_items_between_boxes(
            source_box_id=payload.source_box_id,
            target_box_id=payload.target_box_id,
            product_id=payload.product_id,
            pack_size=payload.pack_size,
            quantity_to_move=payload.quantity,
            net_weight_kg=payload.net_weight_kg,
        )
        return {"message": "Items moved successfully", "details": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/manifests/sync")
def sync_transportation_manifest(
    route_code: str,
    shipment_date: str,
    flight_carrier: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_2)
    ),
):
    """
    Finalized packing list automatically syncs to generate accurate
    transportation manifests / freight invoices for container management.
    """
    service = PackingOverrideService(db)
    manifest = service.sync_transportation_manifest(
        route_code=route_code, shipment_date=shipment_date, flight_carrier=flight_carrier
    )
    return {
        "message": "Transportation manifest synced and locked successfully",
        "manifest_reference": manifest.manifest_reference,
        "total_boxes_count": manifest.total_boxes_count,
        "total_gross_weight_kg": float(manifest.total_gross_weight_kg),
        "total_volumetric_weight_kg": float(manifest.total_volumetric_weight_kg),
        "total_chargeable_weight_kg": float(manifest.total_chargeable_weight_kg),
        "total_cbm": float(manifest.total_cbm),
        "status": manifest.status,
    }


@router.post("/boxes/transfer-contents")
def transfer_box_contents(
    payload: TransferBoxContentsRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Smart Box Resizing Workflow:
    Combines produce items from smaller boxes into a larger box (e.g. Small & Medium into Large).
    Transfers items automatically without requiring manual re-typing of SKUs.
    """
    service = PackingOverrideService(db)
    try:
        result = service.transfer_box_contents(
            source_box_id=payload.source_box_id,
            target_box_id=payload.target_box_id,
            item_ids=payload.item_ids,
            remove_empty_source=payload.remove_empty_source,
            actor=actor,
        )
        return {"message": "Items securely transferred between boxes", "details": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/transportation-manifests/export-vehicle-load")
def export_vehicle_loading(
    route_code: str,
    shipment_date: str,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Box Transportation Export File:
    Generates customer-level box totals (e.g., 'Tete: 10 boxes') for loading verification before vehicle departure.
    """
    service = PackingOverrideService(db)
    return service.generate_vehicle_loading_export(
        route_code=route_code, shipment_date=shipment_date
    )


@router.get("/groups/{group_name}/sub-packing-lists")
def get_group_sub_customer_packing_lists(
    group_name: str,
    shipment_date: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Sub-Customer Extraction for Groups:
    For parent groups like 'CT Group', extracts and itemizes specific sub-customers (like CT-Trung, CT-Cần)
    and returns individual packing lists for each sub-customer.
    """
    clean_grp = group_name.strip()
    q = db.query(ClientOrder).join(Customer, ClientOrder.customer_id == Customer.id)
    if clean_grp.upper() != "ALL":
        q = q.filter(
            (Customer.route_group.ilike(f"%{clean_grp}%"))
            | (Customer.client_code.ilike(f"{clean_grp}-%"))
            | (Customer.client_code.ilike(f"{clean_grp}%"))
        )
    if shipment_date:
        q = q.join(RawOrderFile, ClientOrder.raw_order_file_id == RawOrderFile.id).filter(
            RawOrderFile.shipment_target_date == shipment_date
        )

    orders = q.all()
    results = []
    for ord_obj in orders:
        boxes = (
            db.query(Box).filter(Box.client_order_id == ord_obj.id).order_by(Box.box_number).all()
        )
        main_code = "TT"
        if ord_obj.main_customer:
            mc = ord_obj.main_customer.client_code
            main_code = "TT" if ("TONIO" in mc or "TETE" in mc) else mc.split("-")[0]
        elif ord_obj.raw_file and ord_obj.raw_file.route_line:
            rl = ord_obj.raw_file.route_line
            main_code = "TT" if rl == "TETE" else rl

        sub_code = ord_obj.customer.client_code if ord_obj.customer else "SUB"
        total_b = len(boxes) if boxes else 1

        box_items_out = []
        for b in boxes:
            type_name = (
                b.override_box_type.size_name
                if b.override_box_type
                else (b.box_type.size_name if b.box_type else "Custom")
            )
            sticker = f"{main_code}-{sub_code} / {b.box_number} of {total_b}"
            box_items_out.append(
                {
                    "box_id": b.id,
                    "box_number": b.box_number,
                    "box_type_name": type_name,
                    "sticker_label": sticker,
                    "actual_net_weight_kg": float(b.actual_net_weight_kg)
                    if b.actual_net_weight_kg is not None
                    else None,
                    "actual_gross_weight_kg": float(b.actual_gross_weight_kg)
                    if b.actual_gross_weight_kg is not None
                    else None,
                    "is_locked": b.is_locked,
                    "items_count": len(b.items),
                }
            )

        results.append(
            {
                "client_order_id": ord_obj.id,
                "order_ref_id": ord_obj.order_ref_id,
                "sub_customer_code": sub_code,
                "company_name": ord_obj.customer.company_name if ord_obj.customer else "",
                "route_group": ord_obj.customer.route_group if ord_obj.customer else clean_grp,
                "total_ordered_items_count": ord_obj.total_ordered_items_count or 0,
                "total_estimated_net_weight_kg": float(
                    ord_obj.total_estimated_net_weight_kg or 0.0
                ),
                "boxes_count": len(boxes),
                "boxes": box_items_out,
                "note": f"Individual packing list isolated for {sub_code} ({float(ord_obj.total_estimated_net_weight_kg or 0.0):.2f} kg). Staff can physically merge boxes manually if needed.",
            }
        )

    return {
        "group_name": group_name,
        "total_sub_customers": len(results),
        "total_weight_kg": round(sum(r["total_estimated_net_weight_kg"] for r in results), 2),
        "total_boxes_count": sum(r["boxes_count"] for r in results),
        "sub_customers": results,
    }


@router.post("/orders/{client_order_id}/fast-track-pack")
def fast_track_pack_order(
    client_order_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Template-Driven Fast-Track Packing:
    Populates box manifests directly from template weights and pack units, locks them,
    and returns a signed Handoff 02 so invoicing can proceed immediately without scale friction.
    """
    service = PackingOverrideService(db)
    try:
        return service.fast_track_template_pack_order(client_order_id=client_order_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups/{group_name}/fast-track-pack")
def fast_track_pack_group(
    group_name: str,
    shipment_date: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Bulk Template-Driven Fast-Track Packing:
    Fast-tracks an entire route group (e.g. GROUP CT or ALL) using template weights,
    enabling 1-click packing and instant billing unblocking.
    """
    service = PackingOverrideService(db)
    try:
        return service.fast_track_template_pack_group(
            group_name=group_name, shipment_date=shipment_date, actor=actor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

