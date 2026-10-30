"""Observed revisions at write boundaries; row locks last through request commit."""

from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.models.invoice import Invoice
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box

Revision = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]


class RevisionRequest(BaseModel):
    expected_revision: Revision | None = None


class RevisionConflict(Exception):
    def __init__(self, resource: str, identity: str, current: int, *, missing=False):
        self.status_code = 428 if missing else 409
        self.detail = {
            "code": "REVISION_REQUIRED" if missing else "STALE_REVISION",
            "message": ("Reload this record before saving; its revision is required." if missing else
                        "Someone changed this record. Your changes were not saved. Reload and review the latest record before trying again."),
            "resource": resource, "resource_id": identity, "current_revision": current,
        }
        super().__init__(self.detail["message"])


def require_revision(resource: str, identity: str, current: int, expected: int | None):
    if expected is None or expected != current:
        raise RevisionConflict(resource, identity, current, missing=expected is None)


def lock_invoice(db: Session, invoice_id: str, expected: int | None) -> Invoice:
    invoice = (db.query(Invoice).filter_by(id=invoice_id).with_for_update()
               .options(selectinload(Invoice.items)).populate_existing().first())
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    require_revision("invoice", invoice.id, invoice.revision, expected)
    return invoice


def lock_packing(db: Session, order_id: str, expected: int | None) -> ClientOrder:
    order = db.query(ClientOrder).filter_by(id=order_id).with_for_update().populate_existing().first()
    if order is None:
        raise HTTPException(404, "Order not found")
    if order.status in {OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}:
        raise HTTPException(409, "This order is resolved and cannot be packed")
    require_revision("packing", order.id, order.packing_revision, expected)
    return order


def lock_box_packing(db: Session, box_id: str, expected: int | None) -> ClientOrder:
    order_id = db.query(Box.client_order_id).filter_by(id=box_id).scalar()
    if order_id is None:
        raise HTTPException(404, "Box not found; reload the packing list")
    return lock_packing(db, order_id, expected)
