from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import require_access
from app.models.invoice import Invoice, InvoiceStatus
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.user import User, UserRole

router = APIRouter(
    prefix="/operations",
    tags=["Operations dashboard"],
)


@router.get("/summary")
def operations_summary(
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL)),
):
    status_counts = {
        status.value: count
        for status, count in db.query(ClientOrder.status, func.count(ClientOrder.id))
        .group_by(ClientOrder.status)
        .all()
    }
    can_view_accounting = actor.role in {
        UserRole.GENERAL_ACCOUNTING,
        UserRole.OWNER_ADMIN,
    }
    open_balance = None
    if can_view_accounting:
        open_balance = (
            db.query(
                func.coalesce(
                    func.sum(Invoice.total_amount_eur - func.coalesce(Invoice.amount_paid_eur, 0)),
                    0,
                )
            )
            .filter(Invoice.status != InvoiceStatus.CANCELLED)
            .scalar()
        )
    missing_weights = (
        db.query(func.count(Box.id))
        .filter(
            Box.is_locked.is_(False),
            ((Box.actual_net_weight_kg.is_(None)) | (Box.actual_gross_weight_kg.is_(None))),
        )
        .scalar()
    )

    def count(*statuses: OrderStatus) -> int:
        return sum(status_counts.get(status.value, 0) for status in statuses)

    result = {
        "active_orders": sum(status_counts.values())
        - status_counts.get(OrderStatus.ORDER_CLOSED.value, 0),
        "closed_orders": status_counts.get(OrderStatus.ORDER_CLOSED.value, 0),
        "awaiting_procurement": count(
            OrderStatus.SPLIT_AND_VALIDATED,
            OrderStatus.PURCHASING_CONSOLIDATED,
            OrderStatus.PROCUREMENT_CONFIRMED,
        ),
        "awaiting_packing": count(
            OrderStatus.HANDOFF_01_SENT,
            OrderStatus.PACKING_DESIGNED,
            OrderStatus.FLOOR_PACKING,
            OrderStatus.FINAL_PACK_LOCKED,
        ),
        "boxes_missing_weights": int(missing_weights or 0),
        "orders_by_status": status_counts,
    }
    if can_view_accounting:
        result.update(
            {
                "awaiting_billing": count(
                    OrderStatus.HANDOFF_02_RETURNED,
                    OrderStatus.INVOICES_DRAFTED,
                    OrderStatus.INVOICES_SENT,
                    OrderStatus.CONFIRMED_PAID,
                    OrderStatus.RECEIPTS_ISSUED,
                ),
                "open_balance_eur": float(open_balance or 0),
            }
        )
    return result
