from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.order import OrderStatus
from app.models.procurement import MasterPurchasingList
from app.models.user import HUMAN_ROLES, User
from app.modules.workflow.service import WorkflowService


@dataclass(frozen=True)
class ProcurementConfirmation:
    item_id: str
    purchased_pack_count: int
    purchased_weight_kg: float
    shortage_reason: str | None = None
    replacement_product_id: str | None = None


class ProcurementConfirmationService:
    """Implements the mandatory market-buyer approval gate."""

    ALLOWED_ROLES = HUMAN_ROLES

    def __init__(self, db: Session):
        self.db = db

    def confirm_batch(
        self,
        *,
        batch_id: str,
        confirmations: list[ProcurementConfirmation],
        actor: User,
    ) -> MasterPurchasingList:
        if actor.role not in self.ALLOWED_ROLES or actor.is_service_principal:
            raise PermissionError("A human procurement role must confirm purchases")

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
        if supplied != set(items):
            raise ValueError("A confirmation is required for every purchasing item")

        for confirmation in confirmations:
            if confirmation.purchased_pack_count < 0 or confirmation.purchased_weight_kg < 0:
                raise ValueError("Confirmed purchase quantities cannot be negative")
            item = items[confirmation.item_id]
            item.confirmed_purchased_pack_count = confirmation.purchased_pack_count
            item.confirmed_purchased_weight_kg = round(confirmation.purchased_weight_kg, 2)
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
        self.db.commit()
        self.db.refresh(batch)
        return batch
