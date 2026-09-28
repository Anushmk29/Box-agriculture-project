from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box, BoxItem
from app.models.user import HUMAN_ROLES, User, UserRole
from app.modules.packing.optimizer import (
    DeterministicPackingOptimizer,
    PackingDemand,
    PackingOption,
)
from app.modules.workflow.service import WorkflowService


class PackingPlanningService:
    """Persists deterministic packing designs for confirmed client orders."""

    def __init__(self, db: Session):
        self.db = db

    def design_order(self, *, order_id: str, actor: User) -> list[Box]:
        if actor.role not in {*HUMAN_ROLES, UserRole.AI_AGENT_2}:
            raise PermissionError("Packing design must be executed by Agent 2")
        order = self.db.query(ClientOrder).filter_by(id=order_id).first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        if order.status != OrderStatus.HANDOFF_01_SENT:
            raise ValueError("Handoff 01 is required before packing design")

        existing = self.db.query(Box).filter_by(client_order_id=order.id).all()
        if any(box.is_locked for box in existing):
            raise ValueError("A locked packing design cannot be replaced")
        for box in existing:
            self.db.delete(box)
        self.db.flush()

        catalogues = self.db.query(BoxCatalogue).filter(BoxCatalogue.is_active.is_(True)).all()
        options = [
            PackingOption(
                id=catalogue.id,
                material=catalogue.box_type,
                max_safe_net_weight_kg=float(catalogue.max_safe_net_weight_kg),
                internal_volume_cm3=float(catalogue.volume_m3) * 1_000_000,
            )
            for catalogue in catalogues
        ]
        demands = []
        for item in order.items:
            if item.confirmed_pack_count is None or item.confirmed_net_weight_kg is None:
                raise ValueError("Handoff 01 confirmed quantities are required for packing")
            count = item.confirmed_pack_count
            if count == 0:
                continue
            if not item.product.default_unit_volume_cm3:
                raise ValueError(
                    f"Product {item.product.t_code} requires default unit volume master data"
                )
            unit_weight = float(item.confirmed_net_weight_kg) / count
            demands.append(
                PackingDemand(
                    product_id=item.product_id,
                    pack_size=item.pack_size,
                    pack_count=count,
                    unit_weight_kg=unit_weight,
                    box_material=item.product.box_material,
                    temperature=item.product.temperature,
                    control_status=item.product.control_status,
                    produce_type=item.product.produce_type,
                    weight_class=item.product.weight_class,
                    botanical_family=item.product.botanical_family,
                    unit_volume_cm3=float(item.product.default_unit_volume_cm3),
                )
            )

        route_code = (
            order.customer.route_group
            if order.customer and order.customer.route_group
            else order.raw_file.route_line
        )
        plan = DeterministicPackingOptimizer().solve(
            demands=demands,
            options=options,
            route_code=route_code,
        )
        persisted: list[Box] = []
        for box_number, planned in enumerate(plan.boxes, start=1):
            box_id = str(uuid.uuid4())
            qr_code_uid = (
                f"BOX-{plan.route_code}-{order.raw_file.shipment_target_date.replace('-', '')}-"
                f"{box_number:03d}-{box_id[:8]}"
            )
            box = Box(
                id=box_id,
                client_order_id=order.id,
                box_catalogue_id=planned.option.id,
                box_number=box_number,
                shipment_date=order.raw_file.shipment_target_date,
                route_code=plan.route_code,
                is_mixed_box=len(planned.items) > 1,
                qr_code_uid=qr_code_uid,
                qr_image_url=(f"{settings.PUBLIC_APP_URL.rstrip('/')}/scan/{qr_code_uid}"),
            )
            self.db.add(box)
            self.db.flush()
            for item in planned.items:
                self.db.add(
                    BoxItem(
                        box_id=box.id,
                        product_id=item.product_id,
                        pack_size=item.pack_size,
                        final_packed_count=item.pack_count,
                        final_net_weight_kg=item.net_weight_kg,
                    )
                )
            persisted.append(box)

        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.PACKING_DESIGNED,
            actor=actor,
            target_table="client_orders",
        )
        self.db.commit()
        for box in persisted:
            self.db.refresh(box)
        return persisted

    def start_floor_packing(self, *, order_id: str, actor: User) -> ClientOrder:
        if actor.role not in HUMAN_ROLES:
            raise PermissionError("A floor role must start packing")
        order = self.db.query(ClientOrder).filter_by(id=order_id).first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.FLOOR_PACKING,
            actor=actor,
            target_table="client_orders",
        )
        self.db.commit()
        return order

    def record_box_weights(
        self,
        *,
        box_id: str,
        actual_net_weight_kg: float,
        actual_gross_weight_kg: float,
        capture_source: str,
        actor: User,
    ) -> Box:
        if actor.role not in HUMAN_ROLES:
            raise PermissionError("A floor role must record box weights")
        box = self.db.query(Box).filter_by(id=box_id).with_for_update().first()
        if not box:
            raise ValueError(f"Box {box_id} not found")
        if box.is_locked:
            raise ValueError("Locked box weights are immutable")
        if box.order.status != OrderStatus.FLOOR_PACKING:
            raise ValueError("Box weights can only be recorded during floor packing")
        if actual_net_weight_kg < 0 or actual_gross_weight_kg < 0:
            raise ValueError("Box weights cannot be negative")
        if actual_gross_weight_kg < actual_net_weight_kg:
            raise ValueError("Gross weight cannot be below net weight")

        expected_net = round(sum(float(item.final_net_weight_kg) for item in box.items), 2)
        if abs(actual_net_weight_kg - expected_net) > 0.01:
            raise ValueError("Actual net weight must match the packed item total")
        capacity = float((box.override_box_type or box.box_type).max_safe_net_weight_kg)
        if actual_net_weight_kg > capacity + 0.01:
            raise ValueError("Actual net weight exceeds the box safe capacity")

        before = {
            "actual_net_weight_kg": (
                float(box.actual_net_weight_kg) if box.actual_net_weight_kg is not None else None
            ),
            "actual_gross_weight_kg": (
                float(box.actual_gross_weight_kg)
                if box.actual_gross_weight_kg is not None
                else None
            ),
        }
        box.actual_net_weight_kg = round(actual_net_weight_kg, 2)
        box.actual_gross_weight_kg = round(actual_gross_weight_kg, 2)
        self.db.add(
            AuditLog(
                event_type="BOX_WEIGHT_RECORDED",
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table="boxes",
                target_id=box.id,
                before_state=before,
                after_state={
                    "actual_net_weight_kg": float(box.actual_net_weight_kg),
                    "actual_gross_weight_kg": float(box.actual_gross_weight_kg),
                    "capture_source": capture_source.strip().upper(),
                },
            )
        )
        self.db.commit()
        self.db.refresh(box)
        return box


class FinalPackingService:
    """Owns the transaction that freezes physical packing data."""

    def __init__(self, db: Session):
        self.db = db

    def lock_order(self, *, order_id: str, actor: User) -> list[Box]:
        if actor.role not in {*HUMAN_ROLES, UserRole.AI_AGENT_2}:
            raise PermissionError("Final packing must be locked by Operations or Agent 2")
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
            .all()
        )
        if not boxes:
            raise ValueError("At least one box is required before final-pack confirmation")

        locked_at = datetime.now(UTC)
        for box in boxes:
            if box.actual_net_weight_kg is None or box.actual_gross_weight_kg is None:
                raise ValueError(f"Box {box.box_number} requires final net and gross weights")
            expected_net = round(sum(float(item.final_net_weight_kg) for item in box.items), 2)
            if abs(float(box.actual_net_weight_kg) - expected_net) > 0.01:
                raise ValueError(f"Box {box.box_number} net weight does not match packed items")
            if float(box.actual_gross_weight_kg) < float(box.actual_net_weight_kg):
                raise ValueError(f"Box {box.box_number} gross weight cannot be below net weight")
            capacity = float((box.override_box_type or box.box_type).max_safe_net_weight_kg)
            if float(box.actual_net_weight_kg) > capacity + 0.01:
                raise ValueError(f"Box {box.box_number} exceeds its safe net weight")
            box.weight_utilization_pct = round(float(box.actual_net_weight_kg) / capacity * 100, 2)
            box.packed_by_staff = actor.id
            box.is_locked = True
            box.locked_at = locked_at

        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.FINAL_PACK_LOCKED,
            actor=actor,
            target_table="client_orders",
        )
        self.db.commit()
        return boxes
