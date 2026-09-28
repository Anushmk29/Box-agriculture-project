from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.branding import BRAND_NAME, BRAND_TAGLINE, CURRENCY_SYMBOL, get_logo_base64
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_access
from app.models.invoice import Invoice, Receipt
from app.models.order import ClientOrder, OrderStatus
from app.models.user import User, UserRole
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService

router = APIRouter(
    prefix="/invoicing",
    tags=["Stage 03: Order-to-Cash Settlement"],
    dependencies=[Depends(require_access(UserRole.GENERAL_ACCOUNTING, UserRole.AI_AGENT_1))],
)


class GenerateOnBehalfRequest(BaseModel):
    client_order_id: str
    internal_notes: str | None = None


class RecordPaymentRequest(BaseModel):
    amount_paid: float
    payment_date: str  # 'YYYY-MM-DD'
    bank_name: str
    bank_wire_reference: str
    payment_mode: Literal["BANK_TRANSFER", "CREDIT_LETTER", "CASH"] = "BANK_TRANSFER"
    internal_notes: str | None = None


class InternalNotesUpdateRequest(BaseModel):
    internal_notes: str


class InvoiceLineItemOut(BaseModel):
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
    invoice_number: str
    invoice_type: str
    main_customer_seller: str
    sub_customer_buyer: str
    subtotal_eur: float
    total_amount_eur: float
    amount_paid_eur: float
    status: str
    internal_notes: str | None = None  # Visible to staff
    items: list[InvoiceLineItemOut]


@router.get("/work-queue")
def billing_work_queue(
    db: Session = Depends(get_db),
    actor: User = Depends(
        require_access(UserRole.GENERAL_ACCOUNTING, UserRole.AI_AGENT_1)
    ),
):
    statuses = {
        OrderStatus.HANDOFF_02_RETURNED,
        OrderStatus.INVOICES_DRAFTED,
        OrderStatus.INVOICES_SENT,
        OrderStatus.CONFIRMED_PAID,
        OrderStatus.RECEIPTS_ISSUED,
        OrderStatus.ORDER_CLOSED,
    }
    orders = (
        db.query(ClientOrder)
        .filter(ClientOrder.status.in_(statuses))
        .order_by(ClientOrder.created_at.desc())
        .all()
    )
    invoices = {
        invoice.client_order_id: invoice
        for invoice in db.query(Invoice)
        .filter(Invoice.client_order_id.in_([order.id for order in orders]))
        .all()
    }
    return [
        {
            "order_id": order.id,
            "order_ref_id": order.order_ref_id,
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
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING, UserRole.AI_AGENT_1)),
):
    """
    On-Behalf Invoicing:
    Generates invoices on behalf of the main customer, automatically substituting
    base costs with the main customer's designated selling prices for their sub-customers.
    Strictly derives line items from locked physical final packing scale weights.
    """
    service = OnBehalfInvoicingService(db)
    try:
        invoice = service.generate_sub_customer_invoice(
            client_order_id=payload.client_order_id,
            internal_notes=payload.internal_notes,
            actor=actor,
        )

        return _invoice_detail(invoice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{invoice_id}/approve-dispatch", response_model=InvoiceDetailOut)
def approve_and_dispatch_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    try:
        invoice = OnBehalfInvoicingService(db).approve_and_dispatch(
            invoice_id=invoice_id,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _invoice_detail(invoice)


@router.post("/{invoice_id}/record-payment")
def record_payment_and_generate_receipt(
    invoice_id: str,
    payload: RecordPaymentRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    """
    Financial Processing:
    Staff manually approves payments to trigger receipt generation.
    Maintains private internal notes for administrators to privately track
    rolling balances and specific bank payment dates.
    """
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


@router.put("/{invoice_id}/internal-notes")
def update_invoice_internal_notes(
    invoice_id: str,
    payload: InternalNotesUpdateRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    """
    Internal Notes:
    Private text field for administrators to privately track notes on invoices.
    Never exposed on client-facing export documents.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.internal_notes = payload.internal_notes
    db.commit()
    return {
        "message": "Internal notes updated successfully",
        "invoice_number": invoice.invoice_number,
        "internal_notes": invoice.internal_notes,
    }


@router.get("/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice_detail(
    invoice_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return _invoice_detail(invoice)


@router.get("/{invoice_id}/receipt")
def get_receipt(
    invoice_id: str,
    format: Literal["json", "html"] = "json",
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    receipt = db.query(Receipt).filter(Receipt.invoice_id == invoice_id).first()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if format == "html":
        invoice = receipt.invoice
        seller = invoice.seller_customer
        buyer = invoice.customer
        logo_b64 = get_logo_base64()
        logo_html = f'<img src="data:image/jpeg;base64,{logo_b64}" alt="{escape(BRAND_NAME)}" style="height:60px;object-fit:contain;margin-right:15px;" />' if logo_b64 else ''
        transaction_rows = "".join(
            "<tr>"
            f"<td>{escape(transaction.payment_date)}</td>"
            f"<td>{escape(transaction.bank_wire_reference)}</td>"
            f"<td>{escape(transaction.payment_mode or '')}</td>"
            f"<td style='text-align:right'>THB {CURRENCY_SYMBOL}{float(transaction.amount_paid_eur):.2f}</td>"
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
        <table><thead><tr><th>Payment date</th><th>Bank reference</th><th>Mode</th><th style="text-align:right">Amount (THB)</th></tr></thead><tbody>{transaction_rows}</tbody></table>
        <div class="total">Total received: THB {CURRENCY_SYMBOL}{float(receipt.amount_received_eur):.2f}</div>
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
        "bank_reference": receipt.bank_reference,
        "payload_sha256": receipt.payload_sha256,
        "issued_at": receipt.issued_at,
    }


@router.post("/{invoice_id}/close-order")
def close_paid_order(
    invoice_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    try:
        order = OnBehalfInvoicingService(db).close_paid_order(
            invoice_id=invoice_id,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order_id": order.id, "status": order.status.value}


def _invoice_detail(invoice: Invoice) -> InvoiceDetailOut:
    seller_name = (
        invoice.seller_customer.company_name if invoice.seller_customer else "Main Customer"
    )
    buyer_name = invoice.customer.company_name if invoice.customer else "Sub-Customer"
    item_list = [
        InvoiceLineItemOut(
            product_code=item.product.t_code if item.product else "",
            product_name=f"{item.product.name_vi} / {item.product.name_en}" if item.product else "",
            box_number=item.box_number,
            pack_size=item.pack_size,
            billed_packs=item.billed_pack_count,
            billed_weight_kg=float(item.billed_weight_kg),
            unit_price_eur=float(item.unit_price_eur),
            line_total_eur=float(item.line_total_eur),
        )
        for item in invoice.items
    ]

    return InvoiceDetailOut(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_type=invoice.invoice_type,
        main_customer_seller=seller_name,
        sub_customer_buyer=buyer_name,
        subtotal_eur=float(invoice.subtotal_eur),
        total_amount_eur=float(invoice.total_amount_eur),
        amount_paid_eur=float(invoice.amount_paid_eur or 0.0),
        status=invoice.status.value,
        internal_notes=invoice.internal_notes,
        items=item_list,
    )


@router.get("/{invoice_id}/html")
def render_on_behalf_invoice_html(invoice_id: str, db: Session = Depends(get_db)):
    """
    Renders a printable Commercial Invoice on behalf of the Main Customer (e.g. Tonio Trade Europe BV).
    Features:
    - Main customer letterhead & logo
    - Sub-customer recipient address
    - Line items with selling prices (€4.25) derived from locked final scale weights
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

    seller = invoice.seller_customer
    buyer = invoice.customer
    seller_name = escape(seller.company_name if seller else "Tonio Trade Europe BV")
    seller_email = escape(seller.billing_email if seller else "billing@toniotrade.eu")
    seller_tax_id = escape(seller.tax_vat_id if seller and seller.tax_vat_id else "Not provided")
    buyer_code = escape(buyer.client_code if buyer else "Client")
    buyer_name = escape(buyer.company_name if buyer else buyer_code)
    buyer_route = escape(buyer.route_group if buyer and buyer.route_group else "Standard")
    buyer_destination = escape(
        buyer.destination_country if buyer and buyer.destination_country else "Europe"
    )
    buyer_email = escape(
        buyer.billing_email if buyer and buyer.billing_email else "billing@clienttrade.eu"
    )
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

    rows_html = "".join(
        f"""
    <tr>
        <td style="padding: 10px; border-bottom: 1px solid #eee;">Box #{escape(str(it.box_number))}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: 500;">
            {escape(it.product.t_code if it.product else "")} - {escape(it.product.name_vi if it.product else "")} / {escape(it.product.name_en if it.product else "")}
        </td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: center;">{escape(it.pack_size or "")}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{it.billed_pack_count}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{float(it.billed_weight_kg):.2f} kg</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">{CURRENCY_SYMBOL}{float(it.unit_price_eur):.2f}</td>
        <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right; font-weight: bold;">{CURRENCY_SYMBOL}{float(it.line_total_eur):.2f}</td>
    </tr>
    """
        for it in invoice.items
    )

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
                    <th class="num">Unit Price (THB)</th>
                    <th class="num">Amount (THB)</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div class="totals">
            <div class="total-row">
                <span>Subtotal:</span>
                <span>{CURRENCY_SYMBOL}{float(invoice.subtotal_eur):.2f}</span>
            </div>
            <div class="total-row grand-total">
                <span>Total Amount Due (THB):</span>
                <span>{CURRENCY_SYMBOL}{float(invoice.total_amount_eur):.2f}</span>
            </div>
            <div class="total-row" style="color: #16a34a; font-weight: 600;">
                <span>Amount Paid:</span>
                <span>{CURRENCY_SYMBOL}{float(invoice.amount_paid_eur or 0.0):.2f}</span>
            </div>
            <div class="total-row" style="font-weight: bold; color: {"#dc2626" if float(invoice.total_amount_eur) - float(invoice.amount_paid_eur or 0.0) > 0 else "#16a34a"};">
                <span>Balance Remaining:</span>
                <span>{CURRENCY_SYMBOL}{max(0.0, float(invoice.total_amount_eur) - float(invoice.amount_paid_eur or 0.0)):.2f}</span>
            </div>
        </div>

        <div class="bank-info">
            <h4>Settlement Currency: Thai Baht (THB {CURRENCY_SYMBOL}) · Bank Wire Instructions:</h4>
            {settlement_html}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
