import re
from decimal import Decimal
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.branding import BRAND_NAME, BRAND_TAGLINE, get_logo_base64
from app.core.config import settings
from app.core.database import get_db
from app.core.dates import canonical_day
from app.core.dependencies import require_access, require_action
from app.core.idempotency import run_once
from app.core.policy import Action
from app.core.read_models import LegacyPage, load_invoice_details
from app.core.revisions import Revision, RevisionRequest, lock_invoice
from app.core.values import BankName, BankReference, PackLabel, PositiveMoney, UnicodeText
from app.models.audit import AuditLog
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus, Receipt
from app.models.master_data import Customer, PriceList
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.user import User, UserRole
from app.modules.pricing import PriceCatalogue, _price_pack_key
from app.services.on_behalf_invoicing_service import (
    MissingCustomerDetailsError,
    MissingSellingPricesError,
    OnBehalfInvoicingService,
    _approved_prices,
    _billing_boxes,
    _price_requests,
    invoice_contact,
)

router = APIRouter(
    prefix="/invoicing",
    tags=["Stage 03: Order-to-Cash Settlement"],
    dependencies=[Depends(require_access(UserRole.GENERAL_ACCOUNTING, UserRole.AI_AGENT_1))],
)


class GenerateOnBehalfRequest(BaseModel):
    client_order_id: str
    internal_notes: str | None = None


class GenerateMasterInvoiceRequest(BaseModel):
    client_order_ids: list[str] = Field(min_length=2, max_length=500)
    internal_notes: str | None = None


class BulkInvoicesRequest(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)


class BulkApprovalRequest(BaseModel):
    invoice_ids: list[str] = Field(min_length=1, max_length=500)
    expected_revisions: dict[str, Revision] = Field(default_factory=dict, max_length=500)


class ResolvePriceItem(BaseModel):
    order_id: str
    product_id: str
    pack_size: PackLabel
    selling_price: PositiveMoney


class ResolvePricesRequest(BaseModel):
    items: list[ResolvePriceItem] = Field(min_length=1, max_length=5000)


def _pricing_orders(db: Session, order_ids, *, lock: bool = False):
    ids = set(order_ids)
    query = db.query(ClientOrder).options(
        joinedload(ClientOrder.raw_file).load_only(RawOrderFile.file_name, RawOrderFile.shipment_target_date),
        joinedload(ClientOrder.customer).load_only(Customer.company_name, Customer.client_code),
    ).filter(ClientOrder.id.in_(ids)).order_by(ClientOrder.id)
    orders = (query.with_for_update(of=ClientOrder) if lock else query).all()
    if len(orders) != len(ids):
        raise HTTPException(404, "Order not found")
    if any(order.status != OrderStatus.HANDOFF_02_RETURNED for order in orders):
        raise HTTPException(409, "Only completed packing awaiting an invoice can have missing prices resolved")
    result = {order.id: (order, []) for order in orders}
    for box in _billing_boxes(db, ids):
        result[box.client_order_id][1].append(box)
    if any(not boxes or any(not box.is_locked for box in boxes) for _, boxes in result.values()):
        raise HTTPException(409, "Locked final packing is required before resolving billing prices")
    return result


@router.post("/price-check")
def check_billing_prices(payload: BulkInvoicesRequest, db: Session = Depends(get_db, scope="function")):
    issues = []
    orders = _pricing_orders(db, payload.order_ids)
    catalogue = PriceCatalogue(db)
    catalogue.preload([lookup for order, boxes in orders.values() for lookup in _price_requests(order, boxes)])
    for order_id in dict.fromkeys(payload.order_ids):
        order, boxes = orders[order_id]
        try:
            _approved_prices(catalogue, order, boxes)
        except MissingSellingPricesError as exc:
            issues.append({**exc.detail(), "order_ref": order.order_ref_id, "file_name": order.raw_file.file_name,
                           "customer_name": order.customer.company_name or order.customer.client_code})
    return {"issues": issues}


@router.post("/resolve-prices")
def resolve_billing_prices(payload: ResolvePricesRequest, db: Session = Depends(get_db, scope="function"),
                           actor: User = Depends(require_action(Action.MANAGE_BILLING))):
    catalogue = PriceCatalogue(db)
    orders = _pricing_orders(db, [item.order_id for item in payload.items], lock=True)
    # A permanent price can serve several selected shipment dates. Serialize
    # catalogue writers for the same account, including missing rows, before
    # checking that a price is still missing. All checks precede any assignments.
    root_ids = {order.main_customer_id or order.customer_id for order, _ in orders.values()}
    db.query(Customer).filter(Customer.id.in_(root_ids)).order_by(Customer.id).with_for_update().all()
    catalogue.preload([lookup for order, boxes in orders.values() for lookup in _price_requests(order, boxes)])
    seen = set()
    planned = {}
    for item in payload.items:
        key = (item.order_id, item.product_id, item.pack_size)
        if key in seen:
            raise HTTPException(422, "Duplicate price entry")
        seen.add(key)
        order, boxes = orders[item.order_id]
        packed = {(line.product_id, line.pack_size) for box in boxes for line in box.items}
        if (item.product_id, item.pack_size) not in packed:
            raise HTTPException(422, "Price entry does not match a product and pack size in this order")
        day = order.raw_file.shipment_target_date
        scope = (order.main_customer_id, order.customer_id, item.product_id, _price_pack_key(item.pack_size))
        if scope in planned and planned[scope][0].selling_price != item.selling_price:
            raise HTTPException(422, "Use one permanent price for the same customer, product and pack size across selected shipments")
        existing = catalogue.price_for(main_customer_id=order.main_customer_id,
            sub_customer_id=order.customer_id, product_id=item.product_id, pack_size=item.pack_size, as_of=day)
        if existing is not None:
            raise HTTPException(409, "A price is already available. Reload the resolution page before saving")
        if Decimal(str(item.selling_price)).quantize(Decimal("0.01")) <= 0:
            raise HTTPException(422, "Selling prices must be at least 0.01")
        if scope not in planned or day < planned[scope][2]:
            planned[scope] = (item, order, day)

    for item, order, day in planned.values():
        if order.main_customer_id:
            catalogue.set_price(actor=actor, main_customer_id=order.main_customer_id,
                sub_customer_id=order.customer_id, product_id=item.product_id, pack_size=item.pack_size,
                price=item.selling_price, valid_from=day)
        else:
            row = PriceList(product_id=item.product_id, customer_id=order.customer_id,
                pack_size=item.pack_size, unit_price_eur=item.selling_price, valid_from=day, valid_to=None)
            db.add(row)
            db.flush()
            db.add(AuditLog(event_type="SELLING_PRICE_SET", actor_id=actor.id, actor_role=actor.role.value,
                target_table="price_lists", target_id=row.id, after_state={"order_id": order.id,
                "pack_size": item.pack_size, "selling_price": float(item.selling_price), "valid_from": day, "valid_to": None}))
    db.flush()
    return {"saved": len(payload.items)}


class RecordPaymentRequest(BaseModel):
    amount_paid: PositiveMoney
    payment_date: str  # 'YYYY-MM-DD'
    bank_name: BankName
    bank_wire_reference: BankReference
    payment_mode: Literal["BANK_TRANSFER", "CREDIT_LETTER", "CASH"] = "BANK_TRANSFER"
    internal_notes: str | None = None

    @field_validator("payment_date")
    @classmethod
    def validate_payment_day(cls, value: str) -> str:
        return canonical_day(value)


class InternalNotesUpdateRequest(RevisionRequest):
    internal_notes: str


class InvoiceLinePriceUpdate(BaseModel):
    line_item_id: str
    unit_price_eur: PositiveMoney


class DraftInvoiceUpdateRequest(RevisionRequest):
    internal_notes: str | None = None
    items: list[InvoiceLinePriceUpdate] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def require_unique_lines(self):
        if len({item.line_item_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate invoice line items are not allowed")
        return self


class InvoiceLineItemOut(BaseModel):
    line_item_id: str
    source_order_id: str
    source_order_ref: str
    source_file_name: str
    source_customer_code: str
    product_code: str
    product_name: str
    box_number: int
    pack_size: str
    billed_packs: int
    billed_weight_kg: float
    unit_price_eur: float
    line_total_eur: float


class InvoiceDetailOut(BaseModel):
    invoice_id: str
    revision: int
    client_order_id: str
    invoice_number: str
    invoice_type: str
    main_customer_seller: str
    sub_customer_buyer: str
    subtotal_eur: float
    total_amount_eur: float
    currency: str
    amount_paid_eur: float
    status: str
    internal_notes: str | None = None  # Visible to staff
    source_order_count: int
    items: list[InvoiceLineItemOut]


@router.get("/work-queue")
def billing_work_queue(
    response: Response,
    page: LegacyPage = Depends(),
    db: Session = Depends(get_db, scope="function"),
):
    statuses = {
        OrderStatus.HANDOFF_02_RETURNED,
        OrderStatus.INVOICES_DRAFTED,
        OrderStatus.INVOICES_SENT,
        OrderStatus.CONFIRMED_PAID,
        OrderStatus.RECEIPTS_ISSUED,
        OrderStatus.ORDER_CLOSED,
    }
    query = (db.query(ClientOrder).filter(ClientOrder.status.in_(statuses))
        .options(joinedload(ClientOrder.customer), joinedload(ClientOrder.raw_file)).order_by(ClientOrder.created_at.desc(), ClientOrder.id))
    orders, _ = page.rows(query, response, replacement="/invoicing/orders?view=all")
    ids = [order.id for order in orders]
    direct = select(Invoice.client_order_id.label("order_id"), Invoice.id.label("invoice_id")).where(Invoice.client_order_id.in_(ids))
    sources = select(InvoiceLineItem.source_order_id.label("order_id"), InvoiceLineItem.invoice_id.label("invoice_id")).where(InvoiceLineItem.source_order_id.in_(ids))
    memberships = direct.union(sources).subquery()
    ranked = select(memberships.c.order_id, memberships.c.invoice_id,
        func.row_number().over(partition_by=memberships.c.order_id,
            order_by=(Invoice.created_at.desc(), Invoice.id)).label("position")).join(Invoice, Invoice.id == memberships.c.invoice_id).subquery()
    linked = db.query(ranked.c.order_id, Invoice).join(Invoice, Invoice.id == ranked.c.invoice_id).filter(ranked.c.position == 1).all() if ids else []
    invoices = dict(linked)
    return [
        {
            "order_id": order.id,
            "order_ref_id": order.order_ref_id,
            "file_name": order.raw_file.file_name,
            "customer_code": order.customer.client_code if order.customer else "",
            "customer_name": order.customer.company_name if order.customer else "",
            "order_status": order.status.value,
            "invoice_id": invoices[order.id].id if order.id in invoices else None,
            "invoice_number": (invoices[order.id].invoice_number if order.id in invoices else None),
            "invoice_status": (invoices[order.id].status.value if order.id in invoices else None),
        }
        for order in orders
    ]


@router.post("/generate-on-behalf", response_model=InvoiceDetailOut)
def generate_on_behalf_invoice(
    payload: GenerateOnBehalfRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DRAFT_INVOICE)),
):
    """
    On-Behalf Invoicing:
    Generates invoices on behalf of the main customer, automatically substituting
    base costs with the main customer's designated selling prices for their sub-customers.
    Strictly derives line items from locked final packed item weights.
    """
    service = OnBehalfInvoicingService(db)
    try:
        invoice = service.generate_sub_customer_invoice(
            client_order_id=payload.client_order_id,
            internal_notes=payload.internal_notes,
            actor=actor,
        )

        return _invoice_detail(invoice)
    except MissingSellingPricesError as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/generate-master", response_model=InvoiceDetailOut)
def generate_master_invoice(
    payload: GenerateMasterInvoiceRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DRAFT_INVOICE)),
):
    try:
        invoice = OnBehalfInvoicingService(db).generate_master_invoice(
            payload.client_order_ids,
            internal_notes=payload.internal_notes,
            actor=actor,
        )
        return _invoice_detail(invoice)
    except MissingSellingPricesError as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/generate-bulk", response_model=list[InvoiceDetailOut])
def generate_bulk_invoices(payload: BulkInvoicesRequest, db: Session = Depends(get_db, scope="function"),
                           actor: User = Depends(require_action(Action.DRAFT_INVOICE))):
    try:
        invoices = OnBehalfInvoicingService(db).generate_bulk_invoices(payload.order_ids, actor=actor)
        details = load_invoice_details(db, [invoice.id for invoice in invoices], page=True)
        return [_invoice_detail(details[invoice.id]) for invoice in invoices]
    except MissingSellingPricesError as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/approve-bulk", response_model=list[InvoiceDetailOut])
def approve_bulk_invoices(payload: BulkApprovalRequest, db: Session = Depends(get_db, scope="function"),
                          actor: User = Depends(require_action(Action.MANAGE_BILLING))):
    ids = sorted(set(payload.invoice_ids))
    if set(payload.expected_revisions) - set(ids):
        raise HTTPException(422, "Revision map must contain only selected invoices")
    # Lock in stable order and verify the whole selection before issuing any.
    for invoice_id in ids:
        lock_invoice(db, invoice_id, payload.expected_revisions.get(invoice_id))
    try:
        invoices = OnBehalfInvoicingService(db).approve_bulk_invoices(payload.invoice_ids, actor=actor)
        details = load_invoice_details(db, [invoice.id for invoice in invoices], page=True)
        return [_invoice_detail(details[invoice.id]) for invoice in invoices]
    except MissingCustomerDetailsError as exc:
        raise HTTPException(409, exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("", response_model=list[InvoiceDetailOut])
def list_invoices(
    response: Response,
    page: LegacyPage = Depends(),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DRAFT_INVOICE)),
):
    ids, _ = page.rows(db.query(Invoice.id).order_by(Invoice.created_at.desc(), Invoice.id), response,
                       replacement="/invoicing/records")
    details = load_invoice_details(db, [identity for (identity,) in ids], page=True)
    return [_invoice_detail(details[identity]) for (identity,) in ids]


@router.patch("/{invoice_id}/draft", response_model=InvoiceDetailOut)
def update_draft_invoice(
    invoice_id: str,
    payload: DraftInvoiceUpdateRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    lock_invoice(db, invoice_id, payload.expected_revision)
    try:
        invoice = OnBehalfInvoicingService(db).update_draft(
            invoice_id=invoice_id,
            line_prices={item.line_item_id: item.unit_price_eur for item in payload.items},
            internal_notes=payload.internal_notes,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _invoice_detail(invoice)


@router.post("/{invoice_id}/finalize", response_model=InvoiceDetailOut)
@router.post("/{invoice_id}/approve-dispatch", response_model=InvoiceDetailOut, include_in_schema=False)
def approve_and_dispatch_invoice(
    invoice_id: str,
    payload: RevisionRequest | None = None,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    lock_invoice(db, invoice_id, payload.expected_revision if payload else None)
    try:
        invoice = OnBehalfInvoicingService(db).approve_and_dispatch(
            invoice_id=invoice_id,
            actor=actor,
        )
    except MissingCustomerDetailsError as exc:
        raise HTTPException(409, exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _invoice_detail(invoice)


class ContactValues(BaseModel):
    billing_email: UnicodeText = Field(max_length=255)
    destination_country: UnicodeText = Field(max_length=64)


class ContactCorrection(BaseModel):
    customer_id: str = Field(min_length=1, max_length=36)
    expected: ContactValues
    billing_email: UnicodeText = Field(min_length=3, max_length=255)
    destination_country: UnicodeText = Field(min_length=1, max_length=64)

    @field_validator("billing_email", "destination_country")
    @classmethod
    def clean_contact(cls, value, info):
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        if info.field_name == "billing_email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid billing email address")
        return value


class CustomerDetailsUpdate(RevisionRequest):
    customers: list[ContactCorrection] = Field(min_length=1, max_length=2)


def _contact_response(invoice, customers):
    return {"invoice_id": invoice.id, "revision": invoice.revision, "customers": [
        {"customer_id": customer.id, "client_code": customer.client_code, "company_name": customer.company_name,
         "role": "Seller" if customer.id == invoice.seller_customer_id else "Bill to",
         "billing_email": customer.billing_email, "destination_country": customer.destination_country}
        for customer in customers]}


@router.get("/{invoice_id}/customer-details")
def get_invoice_customer_details(invoice_id: str, db: Session = Depends(get_db, scope="function"),
                                 _actor: User = Depends(require_action(Action.MANAGE_BILLING))):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status != InvoiceStatus.INVOICE_DRAFT:
        raise HTTPException(409, "Customer details can only be changed while reviewing a draft invoice")
    customers = db.query(Customer).filter(Customer.id.in_({invoice.customer_id, invoice.seller_customer_id} - {None}))\
        .order_by(Customer.id).all()
    return _contact_response(invoice, customers)


@router.put("/{invoice_id}/customer-details")
def update_invoice_customer_details(invoice_id: str, payload: CustomerDetailsUpdate,
                                    db: Session = Depends(get_db, scope="function"),
                                    actor: User = Depends(require_action(Action.MANAGE_BILLING))):
    invoice = lock_invoice(db, invoice_id, payload.expected_revision)
    if invoice.status != InvoiceStatus.INVOICE_DRAFT:
        raise HTTPException(409, "Customer details can only be changed while reviewing a draft invoice")
    ids = {invoice.customer_id, invoice.seller_customer_id} - {None}
    if len(payload.customers) != len(ids) or {item.customer_id for item in payload.customers} != ids:
        raise HTTPException(422, "Choose exactly the customers on this invoice")
    customers = db.query(Customer).filter(Customer.id.in_(ids)).order_by(Customer.id)\
        .with_for_update().populate_existing().all()
    corrections = {item.customer_id: item for item in payload.customers}
    for customer in customers:
        expected = corrections[customer.id].expected.model_dump()
        if expected != {key: getattr(customer, key) for key in expected}:
            raise HTTPException(409, detail={"code": "CUSTOMER_DETAILS_CHANGED",
                "message": "Customer details changed. Reload and review them before saving."})
    for customer in customers:
        update = corrections[customer.id]
        before = update.expected.model_dump()
        after = {"billing_email": update.billing_email, "destination_country": update.destination_country}
        if before == after:
            continue
        for key, value in after.items():
            setattr(customer, key, value)
        db.add(AuditLog(event_type="CUSTOMER_BILLING_DETAILS_UPDATED", actor_id=actor.id,
            actor_role=actor.role.value, target_table="customers", target_id=customer.id,
            before_state=before, after_state=after))
    invoice.revision += 1
    db.flush()
    return _contact_response(invoice, customers)


# Historical compatibility helper only; deliberately not an HTTP route in the invoice-only application.
def record_payment_and_generate_receipt(
    invoice_id: str,
    payload: RecordPaymentRequest,
    request_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    """
    Financial Processing:
    Staff manually approves payments to trigger receipt generation.
    Maintains private internal notes for administrators to privately track
    rolling balances and specific bank payment dates.
    """
    def apply():
        service = OnBehalfInvoicingService(db)
        try:
            result = service.record_payment_and_issue_receipt(
                invoice_id=invoice_id,
                amount_paid=payload.amount_paid,
                payment_date=payload.payment_date,
                bank_name=payload.bank_name,
                bank_wire_reference=payload.bank_wire_reference,
                payment_mode=payload.payment_mode,
                internal_notes=payload.internal_notes,
                actor=actor,
            )
            return {"message": "Payment recorded successfully", "payment_details": result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return run_once(db, actor_id=actor.id, operation="invoices.record_payment.v1", key=request_key,
        payload={"target_id": invoice_id, "body": payload.model_dump(mode="json")}, execute=apply)


@router.put("/{invoice_id}/internal-notes")
def update_invoice_internal_notes(
    invoice_id: str,
    payload: InternalNotesUpdateRequest,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    """
    Internal Notes:
    Private text field for administrators to privately track notes on invoices.
    Never exposed on client-facing export documents.
    """
    invoice = lock_invoice(db, invoice_id, payload.expected_revision)
    invoice.internal_notes = payload.internal_notes
    invoice.revision += 1
    db.flush()
    return {
        "message": "Internal notes updated successfully",
        "invoice_number": invoice.invoice_number,
        "internal_notes": invoice.internal_notes,
        "revision": invoice.revision,
    }


@router.get("/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice_detail(
    invoice_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.DRAFT_INVOICE)),
):
    invoice = load_invoice_details(db, [invoice_id]).get(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return _invoice_detail(invoice)


@router.get("/{invoice_id}/lines")
def invoice_lines(invoice_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
                  db: Session = Depends(get_db, scope="function"),
                  actor: User = Depends(require_action(Action.DRAFT_INVOICE))):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    query = db.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice_id)
    total = query.count()
    lines = query.options(joinedload(InvoiceLineItem.product),
        joinedload(InvoiceLineItem.source_order).joinedload(ClientOrder.customer),
        joinedload(InvoiceLineItem.source_order).joinedload(ClientOrder.raw_file)
    ).order_by(InvoiceLineItem.box_number, InvoiceLineItem.id).offset(offset).limit(limit).all()
    return {"invoice_id": invoice.id, "revision": invoice.revision, "total": total, "offset": offset,
        "limit": limit, "has_more": offset + len(lines) < total,
        "items": [_invoice_line(item, invoice.client_order_id) for item in lines]}


# Historical compatibility helper only; deliberately not an HTTP route in the invoice-only application.
def get_receipt(
    invoice_id: str,
    format: Literal["json", "html"] = "json",
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    receipt = db.query(Receipt).filter(Receipt.invoice_id == invoice_id).first()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if format == "html":
        invoice = receipt.invoice
        currency = invoice.currency
        currency_symbol = {"THB": "\u0e3f", "EUR": "\u20ac"}.get(currency, "")
        seller = invoice.seller_customer
        buyer = invoice.customer
        logo_b64 = get_logo_base64()
        logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" alt="{escape(BRAND_NAME)}" style="height:60px;object-fit:contain;margin-right:15px;" />' if logo_b64 else ''
        transaction_rows = "".join(
            "<tr>"
            f"<td>{escape(transaction.payment_date)}</td>"
            f"<td>{escape(transaction.bank_wire_reference)}</td>"
            f"<td>{escape(transaction.payment_mode or '')}</td>"
            f"<td style='text-align:right'>{currency} {currency_symbol}{float(transaction.amount_paid_eur):.2f}</td>"
            "</tr>"
            for transaction in invoice.transactions
        )
        html = f"""
        <!doctype html><html><head><meta charset="utf-8">
        <title>Receipt {escape(receipt.receipt_number)}</title>
        <style>
        body{{font-family:Arial,sans-serif;color:#172033;margin:40px;max-width:900px}}
        header{{display:flex;justify-content:space-between;align-items:center;border-bottom:3px solid #087f71;padding-bottom:20px}}
        .header-brand{{display:flex;align-items:center}}
        h1{{margin:0;color:#087f71;font-size:1.6rem}} .meta{{text-align:right}} .party{{display:grid;grid-template-columns:1fr 1fr;gap:30px;margin:30px 0}}
        table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;border-bottom:1px solid #dbe4e2;text-align:left}}
        .total{{font-size:1.35rem;font-weight:800;text-align:right;margin:24px 0;color:#0f5132}} .hash{{font:11px monospace;overflow-wrap:anywhere;background:#f1f5f9;padding:12px}}
        @media print{{.no-print{{display:none}}body{{margin:16px}}}}
        </style></head><body>
        <button class="no-print" onclick="window.print()" style="padding:10px 18px;background:#087f71;color:#fff;border:none;border-radius:6px;font-weight:bold;cursor:pointer;margin-bottom:15px;">Print / Save PDF</button>
        <header>
            <div class="header-brand">
                {logo_html}
                <div>
                    <h1>OFFICIAL RECEIPT</h1>
                    <div style="font-weight:bold;color:#0f5132;">{escape(BRAND_NAME)}</div>
                    <div style="font-size:12px;color:#64748b;">Issued on behalf of: {escape(seller.company_name if seller else "Main Customer")}</div>
                </div>
            </div>
            <div class="meta"><strong>{escape(receipt.receipt_number)}</strong><br>Invoice {escape(invoice.invoice_number)}<br>{receipt.issued_at.strftime("%Y-%m-%d") if receipt.issued_at else ""}</div>
        </header>
        <section class="party"><div><small>RECEIVED BY</small><h3>{escape(seller.company_name if seller else "Main Customer")}</h3><p>Tax ID: {escape(seller.tax_vat_id if seller and seller.tax_vat_id else "")}</p></div>
        <div><small>RECEIVED FROM</small><h3>{escape(buyer.company_name if buyer else "Customer")}</h3><p>Tax ID: {escape(buyer.tax_vat_id if buyer and buyer.tax_vat_id else "")}</p></div></section>
        <table><thead><tr><th>Payment date</th><th>Bank reference</th><th>Mode</th><th style="text-align:right">Amount ({currency})</th></tr></thead><tbody>{transaction_rows}</tbody></table>
        <div class="total">Total received: {currency} {currency_symbol}{float(receipt.amount_received_eur):.2f}</div>
        <p>This receipt confirms full settlement of the invoice above.</p>
        <div class="hash">Integrity SHA-256: {escape(receipt.payload_sha256 or "")}</div>
        </body></html>
        """
        return HTMLResponse(content=html)
    return {
        "receipt_id": receipt.id,
        "receipt_number": receipt.receipt_number,
        "invoice_id": receipt.invoice_id,
        "customer_id": receipt.customer_id,
        "amount_received_eur": float(receipt.amount_received_eur),
        "currency": receipt.invoice.currency,
        "bank_reference": receipt.bank_reference,
        "payload_sha256": receipt.payload_sha256,
        "issued_at": receipt.issued_at,
    }


# Historical compatibility helper only; deliberately not an HTTP route in the invoice-only application.
def close_paid_order(
    invoice_id: str,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.MANAGE_BILLING)),
):
    try:
        order = OnBehalfInvoicingService(db).close_paid_order(
            invoice_id=invoice_id,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order_id": order.id, "status": order.status.value}


def _invoice_line(item: InvoiceLineItem, order_id: str) -> InvoiceLineItemOut:
    return InvoiceLineItemOut(
        line_item_id=item.id, source_order_id=item.source_order_id or order_id,
        source_order_ref=item.source_order.order_ref_id if item.source_order else "",
        source_file_name=item.source_order.raw_file.file_name if item.source_order else "",
        source_customer_code=item.source_order.customer.client_code if item.source_order and item.source_order.customer else "",
        product_code=item.product.t_code if item.product else "",
        product_name=f"{item.product.name_vi} / {item.product.name_en}" if item.product else "",
        box_number=item.box_number, pack_size=item.pack_size, billed_packs=item.billed_pack_count,
        billed_weight_kg=float(item.billed_weight_kg), unit_price_eur=float(item.unit_price_eur),
        line_total_eur=float(item.line_total_eur))


def _invoice_detail(invoice: Invoice) -> InvoiceDetailOut:
    seller_name = (
        invoice.seller_customer.company_name if invoice.seller_customer else "Main Customer"
    )
    source_orders = {
        item.source_order_id: item.source_order
        for item in invoice.items
        if item.source_order_id and item.source_order
    }
    buyer_name = (
        f"{len(source_orders)} sub-customers / orders"
        if invoice.invoice_type == "MASTER_SUMMARY"
        else invoice.customer.company_name if invoice.customer else "Sub-Customer"
    )
    item_list = [_invoice_line(item, invoice.client_order_id) for item in invoice.items]

    return InvoiceDetailOut(
        invoice_id=invoice.id,
        revision=invoice.revision,
        client_order_id=invoice.client_order_id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        currency=invoice.currency,
        main_customer_seller=seller_name,
        sub_customer_buyer=buyer_name,
        subtotal_eur=float(invoice.subtotal_eur),
        total_amount_eur=float(invoice.total_amount_eur),
        amount_paid_eur=float(invoice.amount_paid_eur or 0.0),
        status=invoice.status.value,
        internal_notes=invoice.internal_notes,
        source_order_count=len(source_orders) or 1,
        items=item_list,
    )


@router.get("/{invoice_id}/html")
def render_on_behalf_invoice_html(invoice_id: str, db: Session = Depends(get_db, scope="function")):
    """
    Renders a printable Commercial Invoice on behalf of the Main Customer (e.g. Tonio Trade Europe BV).
    Features:
    - Main customer letterhead & logo
    - Sub-customer recipient address
    - Line items with selling prices (€4.25) derived from locked final packed item weights
    - Bank wire details for settlement
    - Strictly omits internal notes (enforcing privacy boundary)
    """
    invoice = (
        db.query(Invoice)
        .filter((Invoice.id == invoice_id) | (Invoice.invoice_number == invoice_id))
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice = load_invoice_details(db, [invoice.id])[invoice.id]
    currency = invoice.currency
    currency_symbol = {"THB": "\u0e3f", "EUR": "\u20ac"}.get(currency, "")
    seller = invoice.seller_customer
    buyer = invoice.customer
    seller_name = escape(seller.company_name if seller else "Not provided")
    seller_email = escape(invoice_contact(db, invoice, seller)["billing_email"])
    seller_tax_id = escape(seller.tax_vat_id if seller and seller.tax_vat_id else "Not provided")
    buyer_code = escape(buyer.client_code if buyer else "Client")
    buyer_name = escape(buyer.company_name if buyer else buyer_code)
    buyer_route = escape(buyer.route_group if buyer and buyer.route_group else "Standard")
    buyer_contact = invoice_contact(db, invoice, buyer)
    buyer_destination = escape(buyer_contact["destination_country"] or "Not provided")
    buyer_email = escape(buyer_contact["billing_email"])
    invoice_number = escape(invoice.invoice_number)
    invoice_status = escape(invoice.status.value)
    if (
        settings.INVOICE_BANK_NAME.strip()
        and settings.INVOICE_BANK_ACCOUNT_NAME.strip()
        and settings.INVOICE_BANK_SWIFT.strip()
    ):
        settlement_html = f"""
            <div><strong>Bank:</strong> {escape(settings.INVOICE_BANK_NAME.strip())}</div>
            <div><strong>Account Name:</strong> {escape(settings.INVOICE_BANK_ACCOUNT_NAME.strip())}</div>
            <div><strong>SWIFT / BIC:</strong> {escape(settings.INVOICE_BANK_SWIFT.strip())}</div>
            <div><strong>Reference:</strong> {invoice_number}</div>
        """
    else:
        settlement_html = (
            "<div>Settlement instructions will be supplied separately by the authorized "
            "accounting contact.</div>"
        )

    logo_b64 = get_logo_base64()
    logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" alt="{escape(BRAND_NAME)}" style="height: 64px; max-width: 140px; object-fit: contain; margin-right: 18px;" />' if logo_b64 else ''

    rows = []
    current_source = None
    for it in sorted(
        invoice.items,
        key=lambda item: (
            (item.source_order.order_ref_id if item.source_order else ""),
            item.box_number,
        ),
    ):
        if invoice.invoice_type == "MASTER_SUMMARY" and it.source_order_id != current_source:
            current_source = it.source_order_id
            source = it.source_order
            customer_code = source.customer.client_code if source and source.customer else "Customer"
            source_file = source.raw_file.file_name if source else "Source filename unavailable"
            rows.append(
                '<tr class="order-section"><td colspan="7">'
                f"{escape(customer_code)} &middot; {escape(source_file)}</td></tr>"
            )
        rows.append(f"""
    <tr>
        <td style="padding: 10px; border-bottom: 1px solid #eee;">Box #{escape(str(it.box_number))}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: 500;">
            {escape(it.product.t_code if it.product else "")} - {escape(it.product.name_vi if it.product else "")} / {escape(it.product.name_en if it.product else "")}
        </td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: center;">{escape(it.pack_size or "")}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{it.billed_pack_count}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{float(it.billed_weight_kg):.2f} kg</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{currency_symbol}{float(it.unit_price_eur):.2f}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right; font-weight: bold;">{currency_symbol}{float(it.line_total_eur):.2f}</td>
    </tr>
    """)
    rows_html = "".join(rows)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Invoice {invoice_number}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 40px; color: #1e293b; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 30px; }}
            .header-brand {{ display: flex; align-items: center; }}
            .brand {{ font-size: 22px; font-weight: 800; color: #047857; letter-spacing: -0.01em; }}
            .inv-meta {{ text-align: right; }}
            .inv-title {{ font-size: 26px; font-weight: 800; color: #0f172a; margin: 0 0 5px 0; }}
            .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase; background: #dbeafe; color: #1e40af; }}
            .addresses {{ display: flex; justify-content: space-between; margin-bottom: 35px; }}
            .col {{ width: 45%; }}
            .col h3 {{ font-size: 12px; text-transform: uppercase; color: #64748b; margin-bottom: 8px; letter-spacing: 0.05em; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 30px; }}
            th {{ background: #f8fafc; padding: 12px 10px; text-align: left; font-size: 12px; text-transform: uppercase; color: #475569; border-bottom: 2px solid #e2e8f0; }}
            th.num {{ text-align: right; }}
            .order-section td {{ background: #ecf4e5; color: #1f572f; font-weight: 800; padding: 10px; border-top: 2px solid #c9d9bc; }}
            .totals {{ width: 370px; margin-left: auto; margin-bottom: 40px; }}
            .total-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #f1f5f9; }}
            .grand-total {{ font-size: 20px; font-weight: bold; color: #0f172a; border-top: 2px solid #0f172a; margin-top: 8px; padding-top: 12px; }}
            .bank-info {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; font-size: 13px; }}
            .bank-info h4 {{ margin: 0 0 10px 0; color: #0f172a; }}
            @media print {{
                body {{ margin: 20px; }}
                .no-print {{ display: none; }}
            }}
        </style>
    </head>
    <body>
        <div class="no-print" style="margin-bottom: 20px; text-align: right;">
            <button onclick="window.print()" style="padding: 10px 20px; background: #047857; color: white; border: none; border-radius: 8px; font-weight: bold; cursor: pointer;">
                🖨️ Print Commercial Invoice
            </button>
        </div>
        <div class="header">
            <div class="header-brand">
                {logo_html}
                <div>
                    <div class="brand">{escape(BRAND_NAME)}</div>
                    <div style="font-size: 12px; color: #047857; font-weight: 600;">{escape(BRAND_TAGLINE)}</div>
                    <div style="font-size: 12px; color: #64748b;">Issued on behalf of: <strong>{seller_name}</strong></div>
                </div>
            </div>
            <div class="inv-meta">
                <h1 class="inv-title">COMMERCIAL INVOICE</h1>
                <div style="font-weight: 600; color: #334155; margin-bottom: 4px;">{invoice_number}</div>
                <div style="font-size: 13px; color: #64748b;">Issued: {invoice.created_at.strftime("%d-%b-%Y") if invoice.created_at else "Current"}</div>
                <div style="margin-top: 6px;"><span class="badge">{invoice_status}</span></div>
            </div>
        </div>

        <div class="addresses">
            <div class="col">
                <h3>Seller / Issuer:</h3>
                <strong>{seller_name}</strong><br>
                VAT ID: {seller_tax_id}<br>
                Email: {seller_email}
            </div>
            <div class="col">
                <h3>Billed To (Recipient):</h3>
                <strong>{buyer_name}</strong> ({buyer_code})<br>
                Delivery Route: {buyer_route}<br>
                Destination: {buyer_destination}<br>
                Email: {buyer_email}
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Box #</th>
                    <th>Produce Description</th>
                    <th style="text-align: center;">Packing</th>
                    <th class="num">Billed Packs</th>
                    <th class="num">Net Wt</th>
                    <th class="num">Unit Price ({currency})</th>
                    <th class="num">Amount ({currency})</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="totals">
            <div class="total-row">
                <span>Subtotal:</span>
                <span>{currency_symbol}{float(invoice.subtotal_eur):.2f}</span>
            </div>
            <div class="total-row grand-total">
                <span>Total Amount Due ({currency}):</span>
                <span>{currency_symbol}{float(invoice.total_amount_eur):.2f}</span>
            </div>
            <div class="total-row" style="color: #16a34a; font-weight: 600;">
                <span>Amount Paid:</span>
                <span>{currency_symbol}{float(invoice.amount_paid_eur or 0.0):.2f}</span>
            </div>
            <div class="total-row" style="font-weight: bold; color: {"#dc2626" if float(invoice.total_amount_eur) - float(invoice.amount_paid_eur or 0.0) > 0 else "#16a34a"};">
                <span>Balance Remaining:</span>
                <span>{currency_symbol}{max(0.0, float(invoice.total_amount_eur) - float(invoice.amount_paid_eur or 0.0)):.2f}</span>
            </div>
        </div>

        <div class="bank-info">
            <h4>Settlement Currency: ({currency} {currency_symbol}) · Bank Wire Instructions:</h4>
            {settlement_html}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
