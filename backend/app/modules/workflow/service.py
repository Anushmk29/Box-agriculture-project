from __future__ import annotations

from typing import Protocol

from sqlalchemy import tuple_, update
from sqlalchemy.orm import Session, attributes
from sqlalchemy.orm.exc import StaleDataError

from app.core.policy import Action, require
from app.models.audit import AuditLog
from app.models.order import ClientOrder, OrderStatus
from app.models.user import User


class WorkflowTarget(Protocol):
    id: str
    status: OrderStatus


class WorkflowTransitionError(ValueError):
    """Raised when an operation attempts to bypass the order lifecycle."""


NEXT_STATUS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.UPLOADED: frozenset({OrderStatus.PARSED}),
    OrderStatus.PARSED: frozenset({OrderStatus.SPLIT_AND_VALIDATED}),
    OrderStatus.SPLIT_AND_VALIDATED: frozenset({OrderStatus.PURCHASING_CONSOLIDATED}),
    OrderStatus.PURCHASING_CONSOLIDATED: frozenset({OrderStatus.PROCUREMENT_CONFIRMED}),
    OrderStatus.PROCUREMENT_CONFIRMED: frozenset({OrderStatus.HANDOFF_01_SENT}),
    OrderStatus.HANDOFF_01_SENT: frozenset({OrderStatus.PACKING_DESIGNED}),
    OrderStatus.PACKING_DESIGNED: frozenset({OrderStatus.FLOOR_PACKING}),
    OrderStatus.FLOOR_PACKING: frozenset({OrderStatus.FINAL_PACK_LOCKED}),
    OrderStatus.FINAL_PACK_LOCKED: frozenset({OrderStatus.HANDOFF_02_RETURNED}),
    OrderStatus.HANDOFF_02_RETURNED: frozenset({OrderStatus.INVOICES_DRAFTED}),
    OrderStatus.INVOICES_DRAFTED: frozenset({OrderStatus.INVOICES_SENT}),
    OrderStatus.INVOICES_SENT: frozenset({OrderStatus.CONFIRMED_PAID}),
    OrderStatus.CONFIRMED_PAID: frozenset({OrderStatus.RECEIPTS_ISSUED}),
    OrderStatus.RECEIPTS_ISSUED: frozenset({OrderStatus.ORDER_CLOSED}),
    OrderStatus.ORDER_CLOSED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.UNAVAILABLE: frozenset(),
}

# Template packing skips normal procurement and floor packing; it is the only sanctioned shortcut.
FAST_TRACK_SOURCES = frozenset(
    {
        OrderStatus.SPLIT_AND_VALIDATED,
        OrderStatus.PURCHASING_CONSOLIDATED,
        OrderStatus.PROCUREMENT_CONFIRMED,
        OrderStatus.HANDOFF_01_SENT,
        OrderStatus.PACKING_DESIGNED,
        OrderStatus.FLOOR_PACKING,
    }
)


class WorkflowService:
    """Single authority for lifecycle transitions and their audit events.

    Nothing else may assign `status` on an existing order, raw file, or batch;
    rows may only be *created* at their initial status.
    """

    def __init__(self, db: Session, *, defer_flush: bool = False):
        self.db = db
        self.defer_flush = defer_flush

    def terminate(self, *, target, to_status, actor, target_table, reason):
        require(actor, Action.RESOLVE_ORDERS)
        current = OrderStatus(target.status)
        if to_status not in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}:
            raise WorkflowTransitionError("Unsupported order resolution")
        if to_status == OrderStatus.CANCELLED:
            if current not in {OrderStatus.SPLIT_AND_VALIDATED, OrderStatus.PURCHASING_CONSOLIDATED}:
                require(actor, Action.RESOLVE_LATE_CANCELLATION)
        elif current not in {OrderStatus.HANDOFF_01_SENT, OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING}:
            raise WorkflowTransitionError("Unavailable closure requires confirmed procurement allocation")
        if current in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE, OrderStatus.INVOICES_DRAFTED,
                       OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID, OrderStatus.RECEIPTS_ISSUED, OrderStatus.ORDER_CLOSED}:
            raise WorkflowTransitionError("This record is already resolved or invoiced")
        self._write(target, current, to_status, actor, target_table, "ORDER_EXCEPTION_RESOLVED", reason=reason)

    def transition(
        self,
        *,
        target: WorkflowTarget,
        to_status: OrderStatus,
        actor: User,
        target_table: str,
    ) -> None:
        from_status = OrderStatus(target.status)
        if to_status not in NEXT_STATUS[from_status]:
            raise WorkflowTransitionError(
                f"Illegal workflow transition: {from_status.value} -> {to_status.value}"
            )
        self._write(target, from_status, to_status, actor, target_table, "WORKFLOW_TRANSITION")

    def ensure(
        self,
        *,
        target: WorkflowTarget,
        to_status: OrderStatus,
        actor: User,
        target_table: str,
    ) -> None:
        """Transition unless already there; lets idempotent re-runs pass without a second audit row."""
        if OrderStatus(target.status) != to_status:
            self.transition(
                target=target, to_status=to_status, actor=actor, target_table=target_table
            )

    def transition_orders(self, *, orders, to_status: OrderStatus, actor: User) -> None:
        """Advance a shipment together, retaining each order's revision check and audit."""
        orders = list(orders)
        if not orders:
            return
        before = {order.id: (OrderStatus(order.status), order.packing_revision) for order in orders}
        for current, _ in before.values():
            if to_status not in NEXT_STATUS[current]:
                raise WorkflowTransitionError(f"Illegal workflow transition: {current.value} -> {to_status.value}")
        with self.db.no_autoflush:
            updated = dict(self.db.execute(update(ClientOrder).where(
                tuple_(ClientOrder.id, ClientOrder.packing_revision, ClientOrder.status).in_(
                    [(identity, revision, current) for identity, (current, revision) in before.items()]),
            ).values(status=to_status, packing_revision=ClientOrder.packing_revision + 1)
                .returning(ClientOrder.id, ClientOrder.packing_revision)
                .execution_options(synchronize_session=False)).all())
            if len(updated) != len(before):
                raise StaleDataError("Orders changed while purchases were being confirmed; reload the batch")
            for order in orders:
                self._write(order, before[order.id][0], to_status, actor, "client_orders",
                            "WORKFLOW_TRANSITION", flush=False)
                attributes.set_committed_value(order, "status", to_status)
                attributes.set_committed_value(order, "packing_revision", updated[order.id])
        if not self.defer_flush:
            self.db.flush()

    def fast_track_to_final_pack(
        self, *, target: WorkflowTarget, actor: User, target_table: str
    ) -> None:
        from_status = OrderStatus(target.status)
        if from_status not in FAST_TRACK_SOURCES:
            raise WorkflowTransitionError(
                f"Cannot fast-track from {from_status.value}; order is already past packing"
            )
        self._write(
            target,
            from_status,
            OrderStatus.FINAL_PACK_LOCKED,
            actor,
            target_table,
            "WORKFLOW_FAST_TRACK",
        )

    def _write(
        self,
        target: WorkflowTarget,
        from_status: OrderStatus,
        to_status: OrderStatus,
        actor: User,
        target_table: str,
        event_type: str,
        reason: str | None = None,
        *,
        flush: bool = True,
    ) -> None:
        target.status = to_status
        self.db.add(
            AuditLog(
                event_type=event_type,
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table=target_table,
                target_id=target.id,
                before_state={"status": from_status.value},
                after_state={"status": to_status.value, **({"reason": reason} if reason else {})},
            )
        )
        if flush and not self.defer_flush:
            self.db.flush()
