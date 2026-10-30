from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access
from app.core.policy import Action, can, permissions
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList
from app.models.user import User, UserRole

router = APIRouter(
    prefix="/operations",
    tags=["Operations dashboard"],
)


@router.get("/summary")
def operations_summary(
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    status_counts = {
        status.value: count
        for status, count in db.query(ClientOrder.status, func.count(ClientOrder.id))
        .group_by(ClientOrder.status)
        .all()
    }
    can_view_accounting = can(actor, Action.MANAGE_BILLING)
    packing_statuses = {
        OrderStatus.HANDOFF_01_SENT,
        OrderStatus.PACKING_DESIGNED,
        OrderStatus.FLOOR_PACKING,
        OrderStatus.FINAL_PACK_LOCKED,
    }
    def count(*statuses: OrderStatus) -> int:
        return sum(status_counts.get(status.value, 0) for status in statuses)

    result = {
        "permissions": permissions(actor),
        "active_orders": sum(status_counts.values())
        - count(OrderStatus.ORDER_CLOSED, OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID, OrderStatus.RECEIPTS_ISSUED, OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE),
        "closed_orders": status_counts.get(OrderStatus.ORDER_CLOSED.value, 0),
        "awaiting_intake": db.query(func.count(RawOrderFile.id)).filter(
            RawOrderFile.status.in_({OrderStatus.UPLOADED, OrderStatus.PARSED})
        ).scalar() or 0,
        "awaiting_procurement": db.query(func.count(MasterPurchasingList.id)).filter(
            MasterPurchasingList.status.in_(
                {OrderStatus.PURCHASING_CONSOLIDATED, OrderStatus.PROCUREMENT_CONFIRMED}
            )
        ).scalar() or 0,
        "awaiting_packing": count(*packing_statuses),
        "orders_by_status": status_counts,
    }
    if can_view_accounting:
        direct = select(Invoice.client_order_id.label("order_id"))
        sources = select(InvoiceLineItem.source_order_id.label("order_id")).where(InvoiceLineItem.source_order_id.isnot(None))
        memberships = direct.union(sources).subquery()
        ready_orders = db.query(func.count(ClientOrder.id)).filter(
            ClientOrder.status == OrderStatus.HANDOFF_02_RETURNED,
            ~ClientOrder.id.in_(select(memberships.c.order_id).where(memberships.c.order_id.isnot(None))),
        ).scalar() or 0
        actionable_invoices = db.query(func.count(Invoice.id)).filter(
            Invoice.status == InvoiceStatus.INVOICE_DRAFT
        ).scalar() or 0
        result.update(
            {
                "awaiting_billing": ready_orders + actionable_invoices,
                "billing_scope": "INVOICE_ONLY",
                "invoiced_orders": count(OrderStatus.INVOICES_SENT, OrderStatus.CONFIRMED_PAID, OrderStatus.RECEIPTS_ISSUED, OrderStatus.ORDER_CLOSED),
            }
        )
    return result
