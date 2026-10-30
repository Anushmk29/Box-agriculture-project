from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.dependencies import require_access, require_action
from app.core.policy import Action
from app.core.read_models import LegacyPage
from app.core.revisions import Revision, RevisionRequest, lock_box_packing, lock_packing
from app.core.values import INT32_MAX, PACKED_WEIGHT_MAX, PackLabel
from app.models.audit import AuditLog
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.user import User, UserRole
from app.modules.packing.boxes import orders_in_group, sticker_label
from app.modules.packing.constraints import validate_contents
from app.modules.packing.floor import EDITABLE_STATUSES, FloorPackingService
from app.modules.packing.planning import apply_replan, lock_inputs, preview_replan
from app.modules.packing.profiles import packing_details, require_current, snapshot_for
from app.modules.packing.service import FinalPackingService, PackingPlanningService

router = APIRouter(
    prefix="/packing",
    tags=["Stage 02: Physical Packing & Box Management"],
    dependencies=[Depends(require_access(UserRole.GENERAL, UserRole.AI_AGENT_2))],
)


class TransferBoxContentsRequest(RevisionRequest):
    source_box_id: str
    target_box_id: str
    item_ids: list[str] | None = Field(default=None, max_length=1000)
    remove_empty_source: bool = False


class BoxOverrideRequest(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    new_catalogue_id: str | None = None
    override_reason: str | None = None


class AddBoxRequest(RevisionRequest):
    box_catalogue_id: str
    packing_mode: Literal["EL", "NON_EL"] | None = None
    is_mixed_box: bool = True


class MoveItemsRequest(RevisionRequest):
    source_box_id: str
    target_box_id: str
    product_id: str
    pack_size: str
    quantity: int = Field(gt=0, le=INT32_MAX, strict=True)


class PackingDesignRequest(RevisionRequest):
    order_id: str
    packing_mode: Literal["EL", "NON_EL"]


class PackingItemRequest(RevisionRequest):
    product_id: str
    pack_size: PackLabel
    quantity: int = Field(ge=0, le=INT32_MAX, strict=True)
    net_weight_kg: Decimal = Field(ge=0, le=PACKED_WEIGHT_MAX,
                                  max_digits=10, decimal_places=4, allow_inf_nan=False)
    item_id: str | None = None


class RejectPackingRequest(RevisionRequest):
    note: str = Field(min_length=1, max_length=512)


class PackingModeRequest(RevisionRequest):
    packing_mode: Literal["EL", "NON_EL"]


class ReplanApplyRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=4096)


class GroupPackingRequest(BaseModel):
    packing_mode: Literal["EL", "NON_EL"] | None = None
    expected_revisions: dict[str, Revision] = Field(default_factory=dict, max_length=500)


@router.post("/orders/{order_id}/replan-preview")
def replan_preview(order_id: str, payload: PackingModeRequest, db: Session = Depends(get_db, scope="function"),
                   _actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    order = db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
    if not order:
        raise HTTPException(404, "Order not found")
    try:
        return preview_replan(db, order, payload.packing_mode)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/orders/{order_id}/replan-apply")
def replan_apply(order_id: str, payload: ReplanApplyRequest, db: Session = Depends(get_db, scope="function"),
                 actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    order = db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
    if not order:
        raise HTTPException(404, "Order not found")
    try:
        boxes = apply_replan(db, order, payload.preview_token, actor)
        return {"order_id": order.id, "box_ids": [box.id for box in boxes]}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/boxes/{box_id}/packing-mode")
def change_box_packing_mode(box_id: str, payload: PackingModeRequest, db: Session = Depends(get_db, scope="function"),
                            actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    order = lock_box_packing(db, box_id, payload.expected_revision)
    try:
        box = FloorPackingService(db)._editable_box(box_id)
        lock_inputs(db, box.order)
        require_current(box)
        before = box.packing_mode
        box.packing_mode = payload.packing_mode
        validate_contents(box)
        box.packing_snapshot = snapshot_for(box)
        order.packing_revision += 1
        db.add(AuditLog(event_type="BOX_PACKING_MODE_CHANGED", actor_id=actor.id, actor_role=actor.role.value,
            target_table="boxes", target_id=box.id, before_state={"packing_mode": before},
            after_state={"packing_mode": box.packing_mode}))
        return {"box_id": box.id, "packing_mode": box.packing_mode}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/boxes/{box_id}/items")
def save_packing_item(box_id: str, payload: PackingItemRequest,
                      db: Session = Depends(get_db, scope="function"), actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    lock_box_packing(db, box_id, payload.expected_revision)
    try:
        item = FloorPackingService(db).save_item(box_id, actor=actor, **payload.model_dump(exclude={"expected_revision"}))
        return {"item_id": item.id if item else None, "message": "Packing updated; review contents before finalizing"}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/approval-queue")
def packing_approval_queue(response: Response, page: LegacyPage = Depends(), db: Session = Depends(get_db, scope="function"),
                           _actor: User = Depends(require_action(Action.LOCK_FINAL_PACK))):
    query = db.query(ClientOrder).options(joinedload(ClientOrder.customer)).filter(
        ClientOrder.packing_approval_status == "PENDING",
        ClientOrder.status == OrderStatus.FLOOR_PACKING,
    ).order_by(ClientOrder.packing_requested_at, ClientOrder.id)
    orders, _ = page.rows(query, response, replacement="/packing/orders?approval=pending")
    counts = dict(db.query(Box.client_order_id, func.count(Box.id)).filter(
        Box.client_order_id.in_([order.id for order in orders])).group_by(Box.client_order_id).all()) if orders else {}
    return [{"order_id": order.id, "order_ref_id": order.order_ref_id,
             "packing_revision": order.packing_revision,
             "customer_code": order.customer.client_code, "requested_at": order.packing_requested_at,
             "requested_by": order.packing_requested_by,
             "boxes_count": counts.get(order.id, 0)} for order in orders]


@router.post("/orders/{client_order_id}/request-approval")
def request_packing_approval(client_order_id: str, payload: RevisionRequest | None = None, db: Session = Depends(get_db, scope="function"),
                             actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    lock_packing(db, client_order_id, payload.expected_revision if payload else None)
    try:
        order = FinalPackingService(db).request_approval(order_id=client_order_id, actor=actor)
        return {"status": "PENDING", "order_id": order.id}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/orders/{client_order_id}/reject-approval")
def reject_packing_approval(client_order_id: str, payload: RejectPackingRequest,
                            db: Session = Depends(get_db, scope="function"), actor: User = Depends(require_action(Action.LOCK_FINAL_PACK))):
    lock_packing(db, client_order_id, payload.expected_revision)
    try:
        order = FinalPackingService(db).reject_approval(order_id=client_order_id, actor=actor, note=payload.note)
        return {"status": "REJECTED", "order_id": order.id}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/orders/{client_order_id}/prepare")
def prepare_order(client_order_id: str, payload: PackingModeRequest, db: Session = Depends(get_db, scope="function"),
                  actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    lock_packing(db, client_order_id, payload.expected_revision)
    try:
        order = PackingPlanningService(db).prepare_order(order_id=client_order_id, actor=actor, packing_mode=payload.packing_mode)
        return {"order_id": order.id, "status": order.status.value}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/orders/{client_order_id}/finalize")
def finalize_order(client_order_id: str, payload: RevisionRequest | None = None, db: Session = Depends(get_db, scope="function"),
                   actor: User = Depends(require_action(Action.FLOOR_PACKING))):
    lock_packing(db, client_order_id, payload.expected_revision if payload else None)
    try:
        order, handoff = FinalPackingService(db).finalize_order(order_id=client_order_id, actor=actor)
        return {"order_id": order.id, "status": order.status.value, "handoff_id": handoff.id}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/solve-design")
def solve_packing_design(
    payload: PackingDesignRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DESIGN_PACKING)),
):
    lock_packing(db, payload.order_id, payload.expected_revision)
    try:
        boxes = PackingPlanningService(db).design_order(order_id=payload.order_id, actor=actor, packing_mode=payload.packing_mode)
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
    payload: RevisionRequest | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.FLOOR_PACKING)
    ),
):
    lock_packing(db, client_order_id, payload.expected_revision if payload else None)
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
    payload: RevisionRequest | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.LOCK_FINAL_PACK)
    ),
):
    lock_packing(db, client_order_id, payload.expected_revision if payload else None)
    try:
        boxes = FinalPackingService(db).lock_order(order_id=client_order_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "order_id": client_order_id,
        "status": OrderStatus.FINAL_PACK_LOCKED.value,
        "locked_box_ids": [box.id for box in boxes],
    }


class BoxItemOut(BaseModel):
    item_id: str
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
    box_catalogue_id: str
    box_material: str
    safe_net_capacity_kg: float
    allocation_explanation: str
    is_overridden: bool
    actual_gross_weight_kg: float | None
    actual_net_weight_kg: float | None
    volumetric_weight_kg: float | None
    chargeable_weight_kg: float | None
    is_locked: bool
    qr_code_uid: str
    sticker_label: str
    items: list[BoxItemOut]
    packing_mode: str | None = None
    needs_replan: bool = False
    replan_reasons: list[str] = Field(default_factory=list)
    volume_cm3: float = 0
    usable_volume_cm3: float = 0
    volume_utilization_pct: float | None = None
    layers: list[dict] = Field(default_factory=list)


class SubCustomerPackingListOut(BaseModel):
    client_order_id: str
    packing_revision: int
    order_ref_id: str
    status: str
    packing_approval_status: str
    packing_review_note: str | None
    sub_customer_code: str
    route_code: str
    shipment_date: str
    total_boxes_count: int
    boxes: list[BoxDetailOut]


@router.get("/scan/{qr_code_uid}")
def scan_box(qr_code_uid: str, db: Session = Depends(get_db, scope="function"), include_history: bool = True):
    box = db.query(Box).filter(Box.qr_code_uid == qr_code_uid).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box QR code was not found")
    order = box.order
    events = (
        db.query(AuditLog)
        .filter(
            or_(
                (AuditLog.target_table == "boxes") & (AuditLog.target_id == box.id),
                (AuditLog.target_table == "client_orders") & (AuditLog.target_id == order.id),
            )
        )
        .order_by(AuditLog.created_at.desc())
        .all() if include_history else []
    )
    actors = {
        user.id: user
        for user in db.query(User).filter(User.id.in_({event.actor_id for event in events})).all()
    }
    return {
        **packing_details(box),
        "box_id": box.id,
        "qr_code_uid": box.qr_code_uid,
        "box_number": box.box_number,
        "order_id": order.id,
        "packing_revision": order.packing_revision,
        "order_ref_id": order.order_ref_id,
        "order_status": order.status.value,
        "can_edit": order.status in EDITABLE_STATUSES and not box.is_locked,
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
        "change_history": [
            {
                "event_type": event.event_type,
                "actor_name": (
                    actors[event.actor_id].full_name
                    if event.actor_id in actors
                    else event.actor_role.replace("_", " ").title()
                ),
                "actor_role": event.actor_role,
                "changed_at": event.created_at,
                "before": event.before_state,
                "after": event.after_state,
            }
            for event in events
        ],
    }


@router.get("/orders/{client_order_id}/packing-list", response_model=SubCustomerPackingListOut)
def get_sub_customer_packing_list(client_order_id: str, db: Session = Depends(get_db, scope="function")):
    """
    Generates a specific packing list outlining items and box numbers per sub-customer.
    """
    order = db.query(ClientOrder).filter(ClientOrder.id == client_order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="ClientOrder not found")

    boxes = (
        db.query(Box).filter(Box.client_order_id == client_order_id).order_by(Box.box_number).all()
    )

    box_list = []
    for b in boxes:
        box_type = b.override_box_type or b.box_type
        type_name = (b.packing_snapshot or {}).get("catalogue_name", box_type.size_name if box_type else "Custom")
        item_list = [
            BoxItemOut(
                item_id=it.id,
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
                **packing_details(b),
                box_id=b.id,
                box_number=b.box_number,
                box_type_name=type_name,
                box_catalogue_id=box_type.id,
                box_material=(b.packing_snapshot or {}).get("catalogue_material", box_type.box_type.value if box_type else "CUSTOM"),
                safe_net_capacity_kg=(b.packing_snapshot or {}).get("safe_net_capacity_kg", float(box_type.max_safe_net_weight_kg) if box_type else 0),
                allocation_explanation=(
                    "Plan checks weight and occupied volume, preferring fewer compatible boxes. "
                    "products are separated by material, temperature, control/family, produce type and weight class. "
                    f"This box allows {float(box_type.max_safe_net_weight_kg):g} kg net and 92% of its volume. "
                    + ("Staff selected Non-EL: EL-specific segregation is disabled. " if b.packing_mode == "NON_EL" else "Staff selected EL grouping rules. ")
                    + "Floor adjustments may change the initial allocation."
                ),
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
                sticker_label=sticker_label(order, b.box_number, len(boxes)),
                items=item_list,
            )
        )

    return SubCustomerPackingListOut(
        client_order_id=order.id,
        packing_revision=order.packing_revision,
        order_ref_id=order.order_ref_id,
        status=order.status.value,
        packing_approval_status=order.packing_approval_status,
        packing_review_note=order.packing_review_note,
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
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.FLOOR_PACKING)
    ),
):
    """
    Box Management: Floor staff manual override to adjust physical box dimensions or catalogue type
    if inventory differs from the initial plan.
    """
    lock_box_packing(db, box_id, payload.expected_revision)
    service = FloorPackingService(db)
    try:
        updated_box = service.override_box_size(
            box_id=box_id,
            actor=actor,
            new_catalogue_id=payload.new_catalogue_id,
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
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.FLOOR_PACKING)
    ),
):
    """
    Box Management: Allows packers to add an extra box, adjusting total box count.
    """
    lock_packing(db, client_order_id, payload.expected_revision)
    service = FloorPackingService(db)
    try:
        new_box = service.add_extra_box_to_order(
            client_order_id=client_order_id,
            box_catalogue_id=payload.box_catalogue_id,
            actor=actor,
            is_mixed_box=payload.is_mixed_box,
            packing_mode=payload.packing_mode,
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
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.FLOOR_PACKING)
    ),
):
    """Allows packers to reallocate produce items between boxes on the floor."""
    lock_box_packing(db, payload.source_box_id, payload.expected_revision)
    service = FloorPackingService(db)
    try:
        result = service.move_items_between_boxes(
            source_box_id=payload.source_box_id,
            target_box_id=payload.target_box_id,
            product_id=payload.product_id,
            pack_size=payload.pack_size,
            quantity_to_move=payload.quantity,
            actor=actor,
        )
        return {"message": "Items moved successfully", "details": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/manifests/sync")
def sync_transportation_manifest(
    route_code: str,
    shipment_date: str,
    flight_carrier: str | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_access(UserRole.GENERAL, UserRole.AI_AGENT_2)
    ),
):
    """
    Finalized packing list automatically syncs to generate accurate
    transportation manifests / freight invoices for container management.
    """
    service = FloorPackingService(db)
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
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.FLOOR_PACKING)
    ),
):
    """
    Smart Box Resizing Workflow:
    Combines produce items from smaller boxes into a larger box (e.g. Small & Medium into Large).
    Transfers items automatically without requiring manual re-typing of SKUs.
    """
    lock_box_packing(db, payload.source_box_id, payload.expected_revision)
    service = FloorPackingService(db)
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
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    Box Transportation Export File:
    Generates customer-level box totals (e.g., 'Tete: 10 boxes') for loading verification before vehicle departure.
    """
    service = FloorPackingService(db)
    return service.generate_vehicle_loading_export(
        route_code=route_code, shipment_date=shipment_date
    )


@router.get("/groups/{group_name}/sub-packing-lists")
def get_group_sub_customer_packing_lists(
    group_name: str,
    shipment_date: str | None = None,
    db: Session = Depends(get_db, scope="function"),
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
    orders = orders_in_group(db, group_name, shipment_date)
    results = []
    for ord_obj in orders:
        boxes = (
            db.query(Box).filter(Box.client_order_id == ord_obj.id).order_by(Box.box_number).all()
        )
        sub_code = ord_obj.customer.client_code if ord_obj.customer else "SUB"

        box_items_out = []
        for b in boxes:
            type_name = (
                b.override_box_type.size_name
                if b.override_box_type
                else (b.box_type.size_name if b.box_type else "Custom")
            )
            box_items_out.append(
                {
                    "box_id": b.id,
                    "box_number": b.box_number,
                    "box_type_name": type_name,
                    "sticker_label": sticker_label(ord_obj, b.box_number, len(boxes)),
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
                "packing_revision": ord_obj.packing_revision,
                "order_ref_id": ord_obj.order_ref_id,
                "status": ord_obj.status.value,
                "created_at": ord_obj.created_at.isoformat(),
                "outstanding_boxes": sum(not b.is_locked for b in boxes),

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
        "sub_customers": sorted(results, key=lambda item: (item["status"] == "FINAL_PACK_LOCKED", item["created_at"], item["order_ref_id"])),
    }


@router.post("/orders/{client_order_id}/fast-track-pack")
def fast_track_pack_order(
    client_order_id: str,
    payload: PackingModeRequest | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.LOCK_FINAL_PACK)
    ),
):
    """
    Template-Driven Fast-Track Packing:
    Populates box manifests directly from template weights and pack units, locks them,
    and returns a signed Handoff 02 so invoicing can proceed immediately using totals derived from packed items.
    """
    lock_packing(db, client_order_id, payload.expected_revision if payload else None)
    service = FloorPackingService(db)
    try:
        return service.fast_track_template_pack_order(client_order_id=client_order_id, actor=actor, packing_mode=payload.packing_mode if payload else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/groups/{group_name}/fast-track-pack")
def fast_track_pack_group(
    group_name: str,
    shipment_date: str | None = None,
    payload: GroupPackingRequest | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_action(Action.LOCK_FINAL_PACK)
    ),
):
    """
    Bulk Template-Driven Fast-Track Packing:
    Fast-tracks an entire route group (e.g. GROUP CT or ALL) using template weights,
    enabling 1-click packing and instant billing unblocking.
    """
    service = FloorPackingService(db)
    # Freeze the reviewed selection once; never re-query group membership after
    # validating revisions and accidentally include newly arrived orders.
    orders = sorted(orders_in_group(db, group_name, shipment_date), key=lambda order: order.id)
    revisions = payload.expected_revisions if payload else {}
    if set(revisions) - {order.id for order in orders}:
        raise HTTPException(409, "Group membership changed; reload and review the selection")
    for order in orders:
        lock_packing(db, order.id, revisions.get(order.id))
    try:
        results = [service.fast_track_template_pack_order(order.id, actor,
            packing_mode=payload.packing_mode if payload else None) for order in orders]
        return {"group_name": group_name, "orders_packed": len(results),
                "total_boxes": sum(row["boxes_count"] for row in results),
                "total_net_weight_kg": round(sum(row["total_net_weight_kg"] for row in results), 2),
                "orders": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

