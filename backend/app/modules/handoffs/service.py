from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.audit import AgentHandoff
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.procurement import MasterPurchasingList
from app.models.user import User, UserRole
from app.modules.workflow.service import WorkflowService


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class HandoffService:
    """Creates immutable, hashed contracts between the two workflow agents."""

    def __init__(self, db: Session):
        self.db = db

    def dispatch_handoff_01(self, *, batch_id: str, actor: User) -> AgentHandoff:
        if actor.role not in {UserRole.AI_AGENT_1, UserRole.OWNER_ADMIN}:
            raise PermissionError("Handoff 01 must be dispatched by Agent 1")
        batch = self.db.query(MasterPurchasingList).filter_by(id=batch_id).first()
        if not batch:
            raise ValueError(f"Procurement batch {batch_id} not found")
        if batch.status != OrderStatus.PROCUREMENT_CONFIRMED:
            raise ValueError("Procurement must be human-confirmed before Handoff 01")

        orders = self._batch_orders(batch)
        allocated_counts = self._allocate_confirmed_counts(batch, orders)
        confirmed_orders = []
        for order in sorted(orders, key=lambda current: current.order_ref_id):
            positive_items = [item for item in order.items if item.ordered_pack_count > 0]
            for item in positive_items:
                item.confirmed_pack_count = allocated_counts[item.id]
                item.confirmed_net_weight_kg = round(
                    float(item.estimated_net_weight_kg)
                    / item.ordered_pack_count
                    * allocated_counts[item.id],
                    2,
                )
            confirmed_orders.append(
                {
                    "client_id": order.customer_id,
                    "client_name": order.customer.company_name,
                    "order_ref_id": order.order_ref_id,
                    "destination_country": order.customer.destination_country,
                    "items": [
                        {
                            "product_id": item.product_id,
                            "product_code": item.product.t_code,
                            "product_name_en": item.product.name_en,
                            "pack_size": item.pack_size,
                            "confirmed_pack_count": item.confirmed_pack_count,
                            "confirmed_net_weight_kg": float(item.confirmed_net_weight_kg),
                            "box_material_req": item.product.box_material.value,
                            "temperature_req": item.product.temperature.value,
                            "control_status": item.product.control_status.value,
                            "produce_type": item.product.produce_type.value,
                            "weight_class": item.product.weight_class.value,
                        }
                        for item in sorted(
                            positive_items,
                            key=lambda current: (current.product_id, current.pack_size),
                        )
                    ],
                }
            )

        now = datetime.now(UTC)
        handoff_id = str(uuid.uuid4())
        payload = {
            "handoff_id": handoff_id,
            "timestamp": now.isoformat(),
            "shipment_date": batch.shipment_date,
            "route_code": batch.route_code or self._batch_route(batch),
            "procurement_batch_id": batch.id,
            "procurement_confirmations": [
                {
                    "product_id": item.product_id,
                    "pack_size": item.pack_size,
                    "confirmed_pack_count": item.confirmed_purchased_pack_count,
                    "confirmed_weight_kg": float(item.confirmed_purchased_weight_kg or 0),
                    "shortage_reason": item.shortage_reason,
                }
                for item in sorted(
                    batch.items, key=lambda current: (current.product_id, current.pack_size)
                )
            ],
            "confirmed_orders": confirmed_orders,
        }
        handoff = AgentHandoff(
            id=handoff_id,
            handoff_type="HANDOFF_01_ORDER_DATA",
            sender_agent="AI_AGENT_1",
            recipient_agent="AI_AGENT_2",
            payload_json=payload,
            payload_sha256=_canonical_hash(payload),
        )
        self.db.add(handoff)

        workflow = WorkflowService(self.db)
        workflow.transition(
            target=batch,
            to_status=OrderStatus.HANDOFF_01_SENT,
            actor=actor,
            target_table="master_purchasing_lists",
        )
        for order in orders:
            workflow.transition(
                target=order,
                to_status=OrderStatus.HANDOFF_01_SENT,
                actor=actor,
                target_table="client_orders",
            )
        self.db.commit()
        self.db.refresh(handoff)
        return handoff

    def return_handoff_02(self, *, order_id: str, actor: User) -> AgentHandoff:
        if actor.role not in {UserRole.AI_AGENT_2, UserRole.OWNER_ADMIN}:
            raise PermissionError("Handoff 02 must be returned by Agent 2")
        order = self.db.query(ClientOrder).filter_by(id=order_id).first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        if order.status != OrderStatus.FINAL_PACK_LOCKED:
            raise ValueError("Final packing must be locked before Handoff 02")
        boxes = (
            self.db.query(Box).filter_by(client_order_id=order.id).order_by(Box.box_number).all()
        )
        if not boxes or any(not box.is_locked for box in boxes):
            raise ValueError("All boxes must be locked before Handoff 02")

        now = datetime.now(UTC)
        handoff_id = str(uuid.uuid4())
        payload = {
            "handoff_id": handoff_id,
            "timestamp": now.isoformat(),
            "shipment_date": order.raw_file.shipment_target_date,
            "route_code": (
                order.customer.route_group
                if order.customer and order.customer.route_group
                else order.raw_file.route_line
            ),
            "final_packing_manifest_id": order.id,
            "packed_by_staff_id": boxes[0].packed_by_staff,
            "locked_at": max(box.locked_at for box in boxes).isoformat(),
            "total_boxes_count": len(boxes),
            "total_shipment_net_weight_kg": round(
                sum(float(box.actual_net_weight_kg) for box in boxes), 2
            ),
            "total_shipment_gross_weight_kg": round(
                sum(float(box.actual_gross_weight_kg) for box in boxes), 2
            ),
            "boxes": [
                {
                    "box_uuid": box.id,
                    "box_number": box.box_number,
                    "box_type_id": box.override_box_catalogue_id or box.box_catalogue_id,
                    "client_id": order.customer_id,
                    "order_ref_id": order.order_ref_id,
                    "actual_gross_weight_kg": float(box.actual_gross_weight_kg),
                    "actual_net_weight_kg": float(box.actual_net_weight_kg),
                    "rated_weight_kg": float(
                        (box.override_box_type or box.box_type).rated_weight_kg
                    ),
                    "qr_code_url": box.qr_image_url,
                    "items": [
                        {
                            "product_id": item.product_id,
                            "product_code": item.product.t_code,
                            "pack_size": item.pack_size,
                            "final_pack_count": item.final_packed_count,
                            "final_net_weight_kg": float(item.final_net_weight_kg),
                            "floor_adjustment_type": item.adjustment_type.value,
                        }
                        for item in box.items
                    ],
                }
                for box in boxes
            ],
        }
        handoff = AgentHandoff(
            id=handoff_id,
            handoff_type="HANDOFF_02_FINAL_PACKING",
            sender_agent="AI_AGENT_2",
            recipient_agent="AI_AGENT_1",
            payload_json=payload,
            payload_sha256=_canonical_hash(payload),
        )
        self.db.add(handoff)
        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.HANDOFF_02_RETURNED,
            actor=actor,
            target_table="client_orders",
        )
        self.db.commit()
        self.db.refresh(handoff)
        return handoff

    @staticmethod
    def _batch_orders(batch: MasterPurchasingList) -> set[ClientOrder]:
        orders = {source.client_order for source in batch.sources if source.client_order}
        for source in batch.sources:
            if source.raw_order_file:
                orders.update(source.raw_order_file.client_orders)
        return orders

    @staticmethod
    def _batch_route(batch: MasterPurchasingList) -> str | None:
        for source in batch.sources:
            if source.raw_order_file:
                return source.raw_order_file.route_line
        return None

    @staticmethod
    def _allocate_confirmed_counts(
        batch: MasterPurchasingList,
        orders: set[ClientOrder],
    ) -> dict[str, int]:
        """Distribute aggregate market shortages proportionally and deterministically."""
        confirmed = {
            (item.product_id, item.pack_size): int(item.confirmed_purchased_pack_count or 0)
            for item in batch.items
        }
        grouped: dict[tuple[str, str], list] = {}
        for order in orders:
            for item in order.items:
                grouped.setdefault((item.product_id, item.pack_size), []).append(item)

        allocations: dict[str, int] = {}
        for key, items in grouped.items():
            total_requested = sum(item.ordered_pack_count for item in items)
            if total_requested <= 0:
                for item in items:
                    allocations[item.id] = 0
                continue
            target = min(confirmed.get(key, 0), total_requested)
            ranked = []
            allocated = 0
            for item in items:
                exact = item.ordered_pack_count * target / total_requested
                base = math.floor(exact)
                allocations[item.id] = base
                allocated += base
                ranked.append((exact - base, item.client_order.order_ref_id, item.id))
            for _, _, item_id in sorted(ranked, key=lambda value: (-value[0], value[1]))[
                : target - allocated
            ]:
                allocations[item_id] += 1
        return allocations
