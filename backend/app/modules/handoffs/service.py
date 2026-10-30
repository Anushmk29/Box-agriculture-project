from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from app.core.policy import Action, require
from app.models.audit import AgentHandoff
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.procurement import MasterPurchasingList
from app.models.user import User
from app.modules.workflow.service import WorkflowService


def canonical_sha256(payload: dict) -> str:
    """Hash of sorted, compact JSON; the signature for handoffs and receipts."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class HandoffService:
    """Creates immutable, hashed contracts between the two workflow agents."""

    def __init__(self, db: Session):
        self.db = db

    def dispatch_handoff_01(self, *, batch_id: str, actor: User) -> AgentHandoff:
        require(actor, Action.DISPATCH_HANDOFF_01)
        batch = self.db.query(MasterPurchasingList).filter_by(id=batch_id).with_for_update().populate_existing().first()
        if not batch:
            raise ValueError(f"Procurement batch {batch_id} not found")
        if batch.status != OrderStatus.PROCUREMENT_CONFIRMED:
            raise ValueError("Procurement must be human-confirmed before Handoff 01")

        return self.seal_handoff_01(batch=batch, actor=actor)

    def seal_handoff_01(self, *, batch: MasterPurchasingList, actor: User) -> AgentHandoff:
        """Internal transaction step; caller authorizes and locks the batch."""
        orders = self._batch_orders(batch)
        allocated_counts = self._allocate_confirmed_counts(batch, orders)
        purchases = {(item.product_id, item.pack_size): item for item in batch.items}
        confirmed_orders = []
        for order in sorted(orders, key=lambda current: current.order_ref_id):
            positive_items = [item for item in order.items if item.ordered_pack_count > 0]
            for item in positive_items:
                purchase = purchases.get((item.product_id, item.pack_size))
                item.replacement_product_id = purchase.replacement_product_id if purchase else None
                # The relationship may have been loaded before this confirmation.
                self.db.expire(item, ["replacement_product"])
                item.confirmed_pack_count = allocated_counts[item.id]
                item.confirmed_net_weight_kg = (
                    Decimal(str(item.estimated_net_weight_kg))
                    / item.ordered_pack_count
                    * allocated_counts[item.id]
                ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            confirmed_orders.append(
                {
                    "client_id": order.customer_id,
                    "client_name": order.customer.company_name,
                    "order_ref_id": order.order_ref_id,
                    "destination_country": order.customer.destination_country,
                    "items": [
                        {
                            "order_item_id": item.id,
                            "ordered_product_id": item.product_id,
                            "replacement_product_id": item.replacement_product_id,
                            "product_id": item.packing_product.id,
                            "product_code": item.packing_product.t_code,
                            "product_name_en": item.packing_product.name_en,
                            "pack_size": item.pack_size,
                            "confirmed_pack_count": item.confirmed_pack_count,
                            "ordered_pack_count": item.ordered_pack_count,
                            "shortage_pack_count": max(0, item.ordered_pack_count - item.confirmed_pack_count),
                            "confirmed_net_weight_kg": float(item.confirmed_net_weight_kg),
                            "box_material_req": item.packing_product.box_material.value,
                            "temperature_req": item.packing_product.temperature.value,
                            "control_status": item.packing_product.control_status.value,
                            "produce_type": item.packing_product.produce_type.value,
                            "weight_class": item.packing_product.weight_class.value,
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
            "confirmed_by_buyer": batch.confirmed_by_buyer,
            "procurement_confirmations": [
                {
                    "product_id": item.product_id,
                    "pack_size": item.pack_size,
                    "confirmed_pack_count": item.confirmed_purchased_pack_count,
                    "confirmed_weight_kg": float(item.confirmed_purchased_weight_kg or 0),
                    "shortage_reason": item.shortage_reason,
                    "replacement_product_id": item.replacement_product_id,
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
            payload_sha256=canonical_sha256(payload),
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
        self.db.flush()
        self.db.refresh(handoff)
        return handoff

    def return_handoff_02(self, *, order_id: str, actor: User) -> AgentHandoff:
        require(actor, Action.RETURN_HANDOFF_02)
        order = self.db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError(f"Client order {order_id} not found")
        handoff = self.seal_handoff_02(order=order, actor=actor)
        self.db.flush()
        self.db.refresh(handoff)
        return handoff

    def seal_handoff_02(self, *, order: ClientOrder, actor: User) -> AgentHandoff:
        """Signs the locked packing and advances the order; flushes, caller commits."""
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
            "order_fulfillment": [
                {
                    "order_item_id": line.id,
                    "ordered_product_id": line.product_id,
                    "replacement_product_id": line.replacement_product_id,
                    "fulfilled_product_id": line.packing_product.id,
                    "pack_size": line.pack_size,
                    "confirmed_pack_count": line.confirmed_pack_count,
                    "confirmed_net_weight_kg": (
                        float(line.confirmed_net_weight_kg)
                        if line.confirmed_net_weight_kg is not None else None
                    ),
                }
                for line in sorted(order.items, key=lambda current: current.id)
            ],
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
                    "packing_mode": box.packing_mode,
                    "packing_snapshot": box.packing_snapshot,
                    "shipping_dimensions_cm": box.shipping_dimensions_snapshot,
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
            client_order_id=order.id,
            payload_json=payload,
            payload_sha256=canonical_sha256(payload),
        )
        self.db.add(handoff)
        WorkflowService(self.db).transition(
            target=order,
            to_status=OrderStatus.HANDOFF_02_RETURNED,
            actor=actor,
            target_table="client_orders",
        )
        return handoff

    def verified_handoff_02(self, order_id: str) -> AgentHandoff:
        """Latest Handoff 02 for the order, after checking its signature."""
        handoff = (
            self.db.query(AgentHandoff)
            .filter_by(handoff_type="HANDOFF_02_FINAL_PACKING", client_order_id=order_id)
            .order_by(AgentHandoff.created_at.desc())
            .first()
        )
        if not handoff:
            raise ValueError("A Handoff 02 contract is required before invoicing")
        if canonical_sha256(handoff.payload_json) != handoff.payload_sha256:
            raise ValueError("Handoff 02 integrity verification failed")
        return handoff

    @staticmethod
    def _batch_orders(batch: MasterPurchasingList) -> set[ClientOrder]:
        orders = {source.client_order for source in batch.sources if source.client_order}
        for source in batch.sources:
            if source.raw_order_file:
                orders.update(source.raw_order_file.client_orders)
        return {order for order in orders if order.status not in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}}

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
        confirmed = {(item.product_id, item.pack_size): item for item in batch.items}
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
            purchase = confirmed.get(key)
            if not purchase or not purchase.confirmed_purchased_pack_count:
                target = 0
            else:
                unit_weights = {
                    Decimal(str(item.estimated_net_weight_kg)) / item.ordered_pack_count
                    for item in items if item.ordered_pack_count > 0
                }
                if len(unit_weights) != 1:
                    raise ValueError("Conflicting standard pack weights require order review before packing")
                unit_weight = unit_weights.pop()
                if not unit_weight.is_finite() or unit_weight <= 0:
                    raise ValueError("Standard pack weight must be finite and positive")
                available = Decimal(str(purchase.confirmed_purchased_weight_kg or 0))
                if not available.is_finite() or available < 0:
                    raise ValueError("Confirmed purchased weight must be finite and nonnegative")
                # Preserve standard packs. Trimming buffer is not additional demand,
                # and purchased kg must never be replaced with invented packing kg.
                target = min(purchase.confirmed_purchased_pack_count, total_requested,
                             int(available / unit_weight))
            ranked = []
            allocated = 0
            for item in items:
                exact = item.ordered_pack_count * target / total_requested
                base = math.floor(exact)
                allocations[item.id] = base
                allocated += base
                ranked.append((exact - base, item.client_order.order_ref_id, item.id))
            for _, _, item_id in sorted(ranked, key=lambda value: (-value[0], value[1], value[2]))[
                : target - allocated
            ]:
                allocations[item_id] += 1
        return allocations
