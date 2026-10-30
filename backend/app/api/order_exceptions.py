"""Internal order exceptions; invoice creation is the application boundary."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.registers import ORDER_SORTS, order_query, order_summary
from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.idempotency import run_once
from app.core.policy import Action, can, require
from app.core.registers import RegisterQuery
from app.core.revisions import Revision
from app.models.audit import AuditLog
from app.models.invoice import Invoice, InvoiceLineItem
from app.models.order import ClientOrder, OrderStatus
from app.models.user import User
from app.modules.orders.exceptions import BEFORE_BUYING, TERMINAL, resolve_order, zero_allocation

router = APIRouter(prefix="/order-exceptions", tags=["Internal order exceptions"],
    dependencies=[Depends(require_action(Action.RESOLVE_ORDERS))])
SHORTAGE_STAGES = {OrderStatus.HANDOFF_01_SENT, OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING}
PRE_INVOICE = BEFORE_BUYING | SHORTAGE_STAGES | {OrderStatus.PROCUREMENT_CONFIRMED,
    OrderStatus.FINAL_PACK_LOCKED, OrderStatus.HANDOFF_02_RETURNED}


def base_query(db, params):
    view = params.view
    params.view = "all"
    try:
        query = order_query(db, params, set(OrderStatus), set(OrderStatus))
    finally:
        params.view = view
    zero = db.query(ClientOrder.id).filter(*zero_allocation()).subquery()
    is_zero = ClientOrder.id.in_(select(zero.c.id))
    has_invoice = db.query(Invoice.id).filter(or_(Invoice.client_order_id == ClientOrder.id,
        Invoice.id.in_(select(InvoiceLineItem.invoice_id).where(InvoiceLineItem.source_order_id == ClientOrder.id).correlate(ClientOrder)))).exists()
    return query.add_columns(is_zero, has_invoice)


def summary(order, zero, invoiced, actor):
    return {**order_summary(order), "revision": order.packing_revision,
        "can_cancel": order.status in BEFORE_BUYING and not invoiced,
        "can_close_unavailable": order.status in SHORTAGE_STAGES and zero and not invoiced,
        "can_record_manual_cancellation": can(actor, Action.RESOLVE_LATE_CANCELLATION)
            and order.status in PRE_INVOICE and not invoiced}


@router.get("")
def exceptions(params: RegisterQuery = Depends(), db: Session = Depends(get_db, scope="function"),
               actor: User = Depends(require_action(Action.RESOLVE_ORDERS))):
    query = base_query(db, params)
    if params.view == "pending":
        query = query.filter(ClientOrder.status.in_(SHORTAGE_STAGES), *zero_allocation())
    elif params.view == "completed":
        query = query.filter(ClientOrder.status.in_(TERMINAL))
    rows, total = params.rows(query, ORDER_SORTS, ClientOrder.id)
    return params.page([summary(order, zero, invoiced, actor) for order, zero, invoiced in rows], total)


@router.get("/{order_id}")
def exception_detail(order_id: str, params: RegisterQuery = Depends(),
                     db: Session = Depends(get_db, scope="function"), actor: User = Depends(require_action(Action.RESOLVE_ORDERS))):
    row = base_query(db, params).filter(ClientOrder.id == order_id).first()
    if not row:
        raise HTTPException(404, "Order not found")
    result = summary(*row, actor)
    event = db.query(AuditLog).filter_by(target_table="client_orders", target_id=order_id,
        event_type="ORDER_EXCEPTION_RESOLVED").order_by(AuditLog.created_at.desc(), AuditLog.id).first()
    result["resolution_reason"] = (event.after_state or {}).get("reason") if event else None
    return result


class ResolutionRequest(BaseModel):
    outcome: Literal["CANCELLED", "UNAVAILABLE"]
    reason: str = Field(min_length=3, max_length=512)
    expected_revision: Revision
    manual_resolution_confirmed: bool = Field(False, strict=True)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Explain the reason for this resolution")
        return value


@router.post("/{order_id}/resolve")
def resolve(order_id: str, payload: ResolutionRequest, request_key: str | None = Header(None, alias="Idempotency-Key"),
            db: Session = Depends(get_db, scope="function"), actor: User = Depends(require_action(Action.RESOLVE_ORDERS))):
    if payload.manual_resolution_confirmed:
        require(actor, Action.RESOLVE_LATE_CANCELLATION)
    def apply():
        try:
            return resolve_order(db, order_id=order_id, outcome=OrderStatus(payload.outcome),
                reason=payload.reason, expected_revision=payload.expected_revision,
                manual=payload.manual_resolution_confirmed, actor=actor)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return run_once(db, actor_id=actor.id, operation="orders.resolve_exception.v1", key=request_key,
        payload={"order_id": order_id, "body": payload.model_dump(mode="json")}, execute=apply)
