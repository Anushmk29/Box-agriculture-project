from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.policy import Action, require
from app.core.values import INT32_MAX, purchase_weight
from app.models.audit import AgentHandoff, AuditLog
from app.models.master_data import Product
from app.models.order import OrderStatus
from app.models.procurement import MasterPurchasingList
from app.models.user import User
from app.modules.workflow.service import WorkflowService


@dataclass(frozen=True)
class ProcurementConfirmation:
    item_id: str
    purchased_pack_count: int
    purchased_weight_kg: float | Decimal
    shortage_reason: str | None = None
    replacement_product_id: str | None = None


class ProcurementConfirmationService:
    """Implements the mandatory market-buyer approval gate."""

    def __init__(self, db: Session):
        self.db = db

    def confirm_and_dispatch(self, *, batch_id: str, confirmations: list[ProcurementConfirmation], actor: User) -> MasterPurchasingList:
        require(actor, Action.CONFIRM_PROCUREMENT)
        batch = self.db.query(MasterPurchasingList).filter_by(id=batch_id).with_for_update().populate_existing().first()
        if not batch:
            raise ValueError("Procurement batch not found")
        if batch.status == OrderStatus.PURCHASING_CONSOLIDATED:
            self.confirm_batch(batch_id=batch_id, confirmations=confirmations, actor=actor)
        elif confirmations:
            persisted = {item.id: item for item in batch.items}
            if len(confirmations) != len(persisted) or {line.item_id for line in confirmations} != set(persisted):
                raise ValueError("Purchases are already confirmed; reload the batch before continuing")
            for line in confirmations:
                item = persisted[line.item_id]
                if (
                    line.purchased_pack_count != item.confirmed_purchased_pack_count
                    or item.confirmed_purchased_weight_kg is None
                    or purchase_weight(line.purchased_weight_kg) != purchase_weight(item.confirmed_purchased_weight_kg)
                    or (line.shortage_reason is not None and line.shortage_reason != item.shortage_reason)
                    or line.replacement_product_id != item.replacement_product_id
                ):
                    raise ValueError("Purchases are already confirmed with different values; reload the batch")
        if batch.status == OrderStatus.PROCUREMENT_CONFIRMED:
            from app.modules.handoffs.service import HandoffService
            HandoffService(self.db).seal_handoff_01(batch=batch, actor=actor)
        else:
            existing = self.db.query(AgentHandoff).filter(
                AgentHandoff.handoff_type == "HANDOFF_01_ORDER_DATA",
                AgentHandoff.payload_json["procurement_batch_id"].as_string() == batch.id,
            ).first()
            if batch.status != OrderStatus.HANDOFF_01_SENT or not existing:
                raise ValueError("Batch cannot be confirmed in its current state")
        return batch

    def confirm_batch(
        self,
        *,
        batch_id: str,
        confirmations: list[ProcurementConfirmation],
        actor: User,
    ) -> MasterPurchasingList:
        require(actor, Action.CONFIRM_PROCUREMENT)

        batch = (
            self.db.query(MasterPurchasingList)
            .filter(MasterPurchasingList.id == batch_id)
            .with_for_update()
            .first()
        )
        if not batch:
            raise ValueError(f"Procurement batch {batch_id} not found")
        if batch.status != OrderStatus.PURCHASING_CONSOLIDATED:
            raise ValueError("Only a consolidated procurement batch can be confirmed")

        items = {item.id: item for item in batch.items}
        supplied = {confirmation.item_id for confirmation in confirmations}
        if len(supplied) != len(confirmations) or supplied != set(items):
            raise ValueError("A confirmation is required for every purchasing item")

        for confirmation in confirmations:
            purchased_kg = purchase_weight(confirmation.purchased_weight_kg)
            if type(confirmation.purchased_pack_count) is not int or not 0 <= confirmation.purchased_pack_count <= INT32_MAX:
                raise ValueError("Confirmed purchase count must be a supported nonnegative integer")
            if (confirmation.purchased_pack_count == 0) != (purchased_kg == 0):
                raise ValueError("Purchased packs and weight must both be zero or both be positive")
            if confirmation.replacement_product_id is not None:
                replacement = self.db.get(Product, confirmation.replacement_product_id)
                if not replacement or not replacement.is_active:
                    raise ValueError("Select an active replacement product from the catalogue")
                if replacement.id == items[confirmation.item_id].product_id:
                    raise ValueError("A substitute must differ from the originally ordered product")

        for confirmation in confirmations:
            item = items[confirmation.item_id]
            item.confirmed_purchased_pack_count = confirmation.purchased_pack_count
            item.confirmed_purchased_weight_kg = purchase_weight(confirmation.purchased_weight_kg)
            item.shortage_reason = confirmation.shortage_reason
            item.replacement_product_id = confirmation.replacement_product_id

        workflow = WorkflowService(self.db)
        workflow.transition(
            target=batch,
            to_status=OrderStatus.PROCUREMENT_CONFIRMED,
            actor=actor,
            target_table="master_purchasing_lists",
        )
        batch.confirmed_by_buyer = actor.id
        batch.confirmed_at = datetime.now(UTC)

        raw_files = {source.raw_order_file for source in batch.sources if source.raw_order_file}
        direct_orders = {source.client_order for source in batch.sources if source.client_order}
        orders = set(direct_orders)
        for raw_file in raw_files:
            if raw_file.status == OrderStatus.PURCHASING_CONSOLIDATED:
                workflow.transition(
                    target=raw_file,
                    to_status=OrderStatus.PROCUREMENT_CONFIRMED,
                    actor=actor,
                    target_table="raw_order_files",
                )
            orders.update(raw_file.client_orders)
        for order in orders:
            if order.status == OrderStatus.PURCHASING_CONSOLIDATED:
                workflow.transition(
                    target=order,
                    to_status=OrderStatus.PROCUREMENT_CONFIRMED,
                    actor=actor,
                    target_table="client_orders",
                )

        self.db.add(
            AuditLog(
                event_type="PROCUREMENT_CONFIRMED",
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table="master_purchasing_lists",
                target_id=batch.id,
                after_state={
                    "status": OrderStatus.PROCUREMENT_CONFIRMED.value,
                    "confirmed_item_count": len(confirmations),
                },
            )
        )
        self.db.flush()
        self.db.refresh(batch)
        return batch
