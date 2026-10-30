from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.policy import Action, require
from app.models.audit import AuditLog
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.user import User
from app.modules.packing.constraints import validate_contents
from app.modules.packing.planning import generate_plan, lock_inputs, persist_plan
from app.modules.packing.profiles import require_current
from app.modules.packing.weights import derive_box_weights
from app.modules.workflow.service import WorkflowService


class PackingPlanningService:
    """Persists deterministic packing designs for confirmed client orders."""

    def __init__(self, db: Session):
        self.db = db

    def prepare_order(self, *, order_id: str, actor: User, packing_mode: str = "EL") -> ClientOrder:
        require(actor, Action.FLOOR_PACKING)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError("Client order not found")
        if order.status == OrderStatus.HANDOFF_01_SENT:
            self.design_order(order_id=order_id, actor=actor, packing_mode=packing_mode)
        if order.status == OrderStatus.PACKING_DESIGNED:
            self.start_floor_packing(order_id=order_id, actor=actor)
        if order.status not in {OrderStatus.FLOOR_PACKING, OrderStatus.FINAL_PACK_LOCKED, OrderStatus.HANDOFF_02_RETURNED, OrderStatus.INVOICES_DRAFTED, OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID, OrderStatus.RECEIPTS_ISSUED, OrderStatus.ORDER_CLOSED}:
            raise ValueError("Purchases must be sent to packing before preparing boxes")
        return order

    def design_order(self, *, order_id: str, actor: User, packing_mode: str = "EL") -> list[Box]:
        require(actor, Action.DESIGN_PACKING)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        if order.status != OrderStatus.HANDOFF_01_SENT:
            raise ValueError("Handoff 01 is required before packing design")

        lock_inputs(self.db, order)
        plan = generate_plan(self.db, order, packing_mode)
        persisted = persist_plan(self.db, order, plan, packing_mode)

        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.PACKING_DESIGNED,
            actor=actor,
            target_table="client_orders",
        )
        self.db.flush()
        for box in persisted:
            self.db.refresh(box)
        return persisted

    def start_floor_packing(self, *, order_id: str, actor: User) -> ClientOrder:
        require(actor, Action.FLOOR_PACKING)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        WorkflowService(self.db).ensure(
            target=order,
            to_status=OrderStatus.FLOOR_PACKING,
            actor=actor,
            target_table="client_orders",
        )
        self.db.flush()
        return order



class FinalPackingService:
    """Owns the transaction that freezes physical packing data."""

    def __init__(self, db: Session):
        self.db = db

    def request_approval(self, *, order_id: str, actor: User) -> ClientOrder:
        require(actor, Action.FLOOR_PACKING)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().first()
        if not order or order.status != OrderStatus.FLOOR_PACKING:
            raise ValueError("Order must be in floor packing before requesting approval")
        if order.packing_approval_status == "PENDING":
            raise ValueError("Packing approval is already pending")
        boxes = self.db.query(Box).filter_by(client_order_id=order.id).all()
        if not boxes:
            raise ValueError("At least one box is required before requesting approval")
        for box in boxes:
            if not box.items:
                raise ValueError(f"Box {box.box_number} has no packed contents")
            require_current(box)
            validate_contents(box)
        order.packing_approval_status = "PENDING"
        order.packing_requested_by = actor.id
        order.packing_requested_at = datetime.now(UTC)
        order.packing_review_note = None
        self.db.add(AuditLog(event_type="PACKING_APPROVAL_REQUESTED", actor_id=actor.id,
            actor_role=actor.role.value, target_table="client_orders", target_id=order.id,
            after_state={"approval_status": "PENDING", "assigned_role": "OWNER_ADMIN"}))
        self.db.flush()
        return order

    def reject_approval(self, *, order_id: str, actor: User, note: str) -> ClientOrder:
        require(actor, Action.LOCK_FINAL_PACK)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().first()
        if not order or order.packing_approval_status != "PENDING" or order.status != OrderStatus.FLOOR_PACKING:
            raise ValueError("Packing approval is not pending")
        if not note.strip():
            raise ValueError("Explain what must be corrected")
        order.packing_approval_status = "REJECTED"
        order.packing_review_note = note.strip()
        self.db.add(AuditLog(event_type="PACKING_APPROVAL_REJECTED", actor_id=actor.id,
            actor_role=actor.role.value, target_table="client_orders", target_id=order.id,
            after_state={"approval_status": "REJECTED", "note": note.strip()}))
        self.db.flush()
        return order

    def finalize_order(self, *, order_id: str, actor: User):
        require(actor, Action.FLOOR_PACKING)
        from app.modules.handoffs.service import HandoffService
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError("Client order not found")
        handoffs = HandoffService(self.db)
        if order.status in {OrderStatus.HANDOFF_02_RETURNED, OrderStatus.INVOICES_DRAFTED, OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID, OrderStatus.RECEIPTS_ISSUED, OrderStatus.ORDER_CLOSED}:
            return order, handoffs.verified_handoff_02(order.id)
        if order.status == OrderStatus.FLOOR_PACKING:
            self.lock_order(order_id=order_id, actor=actor)
        if order.status != OrderStatus.FINAL_PACK_LOCKED:
            raise ValueError("Order must be in floor packing before finalizing packing")
        handoff = handoffs.seal_handoff_02(order=order, actor=actor)
        self.db.flush()
        return order, handoff

    def lock_order(self, *, order_id: str, actor: User) -> list[Box]:
        require(actor, Action.FLOOR_PACKING)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        if order.status != OrderStatus.FLOOR_PACKING:
            raise ValueError("Order must be in floor packing before it can be locked")
        boxes = (
            self.db.query(Box)
            .filter_by(client_order_id=order.id)
            .order_by(Box.box_number)
            .with_for_update()
            .populate_existing()
            .all()
        )
        if not boxes:
            raise ValueError("At least one box is required before final-pack confirmation")

        lock_inputs(self.db, order)
        locked_at = datetime.now(UTC)
        for box in boxes:
            if not box.items:
                raise ValueError(f"Box {box.box_number} has no packed contents")
            require_current(box)
            validate_contents(box)
        for box in boxes:
            catalogue = box.override_box_type or box.box_type
            box.shipping_dimensions_snapshot = [float(box.custom_length_cm or catalogue.length_cm), float(box.custom_width_cm or catalogue.width_cm), float(box.custom_height_cm or catalogue.height_cm)]
            derive_box_weights(box)
            capacity = float((box.override_box_type or box.box_type).max_safe_net_weight_kg)
            box.weight_utilization_pct = round(float(box.actual_net_weight_kg) / capacity * 100, 2)
            box.packed_by_staff = actor.id
            box.is_locked = True
            box.locked_at = locked_at

        self.db.add(AuditLog(event_type="PACKING_FINALIZED", actor_id=actor.id,
            actor_role=actor.role.value, target_table="client_orders", target_id=order.id,
            after_state={"locked_box_count": len(boxes), "weight_source": "PACKED_ITEMS", "packaging_allowance_kg": 0.5}))

        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.FINAL_PACK_LOCKED,
            actor=actor,
            target_table="client_orders",
        )
        self.db.flush()
        return boxes
