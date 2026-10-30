from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dates import canonical_day
from app.core.policy import Action, require
from app.core.values import money, positive_money, required_text
from app.models.audit import AgentHandoff, AuditLog
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus, PaymentTransaction, Receipt
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import Box
from app.models.user import User, UserRole
from app.modules.handoffs.service import HandoffService, canonical_sha256
from app.modules.packing.constraints import validate_verified_net
from app.modules.pricing import PriceCatalogue
from app.modules.workflow.service import WorkflowService


def _money(value: float | Decimal) -> Decimal:
    return money(value)


def _invoice_number(prefix: str, shipment_date: str, customer_code: str, reference: str) -> str:
    """Fit the customer label to the column without cutting the identifying suffix."""
    header = f"{prefix}-{shipment_date.replace('-', '')}-"
    suffix = f"-{reference[:8]}"
    available = Invoice.invoice_number.type.length - len(header) - len(suffix)
    return f"{header}{customer_code[:available]}{suffix}"


class MissingSellingPricesError(ValueError):
    def __init__(self, order: ClientOrder, items: list[dict]):
        self.order_id = order.id
        self.items = items
        self.shipment_date = order.raw_file.shipment_target_date
        labels = [f"{item['product_code']} {item['product_name']} ({item['pack_size']})" for item in items]
        super().__init__(
            f"No approved selling price exists for: {'; '.join(labels)}. "
            f"Add approved prices effective on {self.shipment_date}, then retry."
        )

    def detail(self) -> dict:
        return {"code": "MISSING_SELLING_PRICES", "message": str(self),
                "order_id": self.order_id, "shipment_date": self.shipment_date, "items": self.items}


def _approved_prices(catalogue: PriceCatalogue, order: ClientOrder, boxes: list[Box]) -> dict:
    prices = {}
    missing = []
    for box in boxes:
        for item in box.items:
            key = (item.product_id, item.pack_size)
            if key in prices:
                continue
            prices[key] = catalogue.price_for(
                main_customer_id=order.main_customer_id,
                sub_customer_id=order.customer_id,
                product_id=item.product_id,
                pack_size=item.pack_size,
                as_of=order.raw_file.shipment_target_date,
            )
            if prices[key] is None:
                missing.append({"product_id": item.product_id, "product_code": item.product.t_code,
                                "product_name": item.product.name_en, "pack_size": item.pack_size})
    if missing:
        raise MissingSellingPricesError(order, missing)
    return prices


class OnBehalfInvoicingService:
    """Owns the Phase 4 invoice, payment, and receipt transaction boundaries."""

    def __init__(self, db: Session):
        self.db = db

    def generate_sub_customer_invoice(
        self,
        client_order_id: str,
        *,
        actor: User,
        internal_notes: str | None = None,
    ) -> Invoice:
        require(actor, Action.DRAFT_INVOICE)
        order = (
            self.db.query(ClientOrder)
            .filter(ClientOrder.id == client_order_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise ValueError(f"ClientOrder {client_order_id} not found")
        if order.status not in {OrderStatus.HANDOFF_02_RETURNED, OrderStatus.INVOICES_DRAFTED}:
            raise ValueError("Handoff 02 with locked final packing is required before invoicing")

        boxes = self.db.query(Box).filter(Box.client_order_id == client_order_id).all()
        if not boxes or any(not box.is_locked for box in boxes):
            raise ValueError(f"Order {client_order_id} does not have locked final packing")

        handoff = HandoffService(self.db).verified_handoff_02(order.id)
        self._assert_packing_matches_handoff(boxes, handoff)

        shipment_date = order.raw_file.shipment_target_date
        invoice_number = _invoice_number("INV", shipment_date, order.customer.client_code, order.id)
        invoice = self.db.query(Invoice).filter(Invoice.client_order_id == order.id).first()
        linked = self.db.query(InvoiceLineItem).filter_by(source_order_id=order.id).first()
        if (invoice and invoice.invoice_type == "MASTER_SUMMARY") or (linked and (not invoice or linked.invoice_id != invoice.id)):
            raise ValueError("Order already belongs to a master invoice")
        is_new = invoice is None
        if invoice and invoice.status != InvoiceStatus.INVOICE_DRAFT:
            raise ValueError("A dispatched invoice is immutable and cannot be regenerated")
        if invoice:
            return invoice
        else:
            prices = _approved_prices(PriceCatalogue(self.db), order, boxes)
            invoice = Invoice(
                invoice_number=invoice_number,
                client_order_id=order.id,
                customer_id=order.customer_id,
                seller_customer_id=order.main_customer_id,
                invoice_type="ON_BEHALF_SUB_CUSTOMER",
                currency="THB",
                final_packing_manifest_id=handoff.payload_sha256,
                subtotal_eur=0,
                total_amount_eur=0,
                internal_notes=internal_notes,
                status=InvoiceStatus.INVOICE_DRAFT,
            )
            self.db.add(invoice)
            self.db.flush()

        subtotal = Decimal("0.00")
        for box in boxes:
            for item in box.items:
                price = prices[(item.product_id, item.pack_size)]
                line_total = _money(item.final_packed_count * price)
                subtotal += line_total
                self.db.add(
                    InvoiceLineItem(
                        invoice_id=invoice.id,
                        source_order_id=order.id,
                        product_id=item.product_id,
                        box_number=box.box_number,
                        pack_size=item.pack_size,
                        billed_pack_count=item.final_packed_count,
                        billed_weight_kg=item.final_net_weight_kg,
                        unit_price_eur=price,
                        line_total_eur=line_total,
                    )
                )

        invoice.subtotal_eur = _money(subtotal)
        invoice.total_amount_eur = _money(subtotal)
        if order.status == OrderStatus.HANDOFF_02_RETURNED:
            WorkflowService(self.db).transition(
                target=order,
                to_status=OrderStatus.INVOICES_DRAFTED,
                actor=actor,
                target_table="client_orders",
            )
        self._audit(
            "EVT_INVOICE_DRAFTED",
            actor,
            "invoices",
            invoice.id,
            before=None if is_new else {"status": InvoiceStatus.INVOICE_DRAFT.value},
            after={"status": InvoiceStatus.INVOICE_DRAFT.value, "total_eur": float(subtotal)},
        )
        self.db.flush()
        self.db.refresh(invoice)
        return invoice

    def generate_bulk_invoices(self, order_ids: list[str], *, actor: User) -> list[Invoice]:
        require(actor, Action.DRAFT_INVOICE)
        ids = sorted(set(order_ids))
        if not ids:
            raise ValueError("Select at least one completed customer order")
        # The request owns one transaction: a failed order rolls back the entire batch.
        return [self.generate_sub_customer_invoice(order_id, actor=actor) for order_id in ids]

    def approve_bulk_invoices(self, invoice_ids: list[str], *, actor: User) -> list[Invoice]:
        self._require_accounting(actor)
        ids = sorted(set(invoice_ids))
        if not ids:
            raise ValueError("Select at least one draft invoice")
        invoices = self.db.query(Invoice).filter(Invoice.id.in_(ids)).with_for_update().all()
        if len(invoices) != len(ids) or any(invoice.status != InvoiceStatus.INVOICE_DRAFT for invoice in invoices):
            raise ValueError("Every selected invoice must be an existing draft")
        return [self.approve_and_dispatch(invoice_id=invoice_id, actor=actor) for invoice_id in ids]

    def generate_master_invoice(
        self,
        client_order_ids: list[str],
        *,
        actor: User,
        internal_notes: str | None = None,
    ) -> Invoice:
        require(actor, Action.DRAFT_INVOICE)
        order_ids = list(dict.fromkeys(client_order_ids))
        if len(order_ids) < 2:
            raise ValueError("Select at least two orders for a master invoice")
        orders = (
            self.db.query(ClientOrder)
            .filter(ClientOrder.id.in_(order_ids))
            .with_for_update()
            .all()
        )
        if len(orders) != len(order_ids):
            raise ValueError("One or more selected orders were not found")
        if any(not order.main_customer_id for order in orders) or len(
            {order.main_customer_id for order in orders}
        ) != 1:
            raise ValueError("A master invoice can only combine orders from the same main customer")
        if any(order.status != OrderStatus.HANDOFF_02_RETURNED for order in orders):
            raise ValueError("Every selected order must have locked final packing ready to invoice")
        already_invoiced = (
            self.db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.source_order_id.in_(order_ids))
            .first()
        ) or self.db.query(Invoice).filter(Invoice.client_order_id.in_(order_ids)).first()
        if already_invoiced:
            raise ValueError("One or more selected orders already belong to an invoice")

        handoffs: dict[str, AgentHandoff] = {}
        boxes_by_order: dict[str, list[Box]] = {}
        for order in orders:
            boxes = self.db.query(Box).filter(Box.client_order_id == order.id).all()
            if not boxes or any(not box.is_locked for box in boxes):
                raise ValueError(f"Order {order.order_ref_id} does not have locked final packing")
            handoff = HandoffService(self.db).verified_handoff_02(order.id)
            self._assert_packing_matches_handoff(boxes, handoff)
            handoffs[order.id] = handoff
            boxes_by_order[order.id] = boxes

        ordered = sorted(orders, key=lambda order: order.order_ref_id)
        manifest_hash = canonical_sha256(
            {
                "orders": [
                    {
                        "order_id": order.id,
                        "handoff_sha256": handoffs[order.id].payload_sha256,
                    }
                    for order in ordered
                ]
            }
        )
        shipment_date = ordered[0].raw_file.shipment_target_date
        customer_code = ordered[0].main_customer.client_code
        catalogue = PriceCatalogue(self.db)
        prices_by_order = {
            order.id: _approved_prices(catalogue, order, boxes_by_order[order.id])
            for order in ordered
        }
        invoice = Invoice(
            invoice_number=_invoice_number("MINV", shipment_date, customer_code, manifest_hash),
            client_order_id=ordered[0].id,
            customer_id=ordered[0].main_customer_id,
            seller_customer_id=ordered[0].main_customer_id,
            invoice_type="MASTER_SUMMARY",
            currency="THB",
            final_packing_manifest_id=manifest_hash,
            subtotal_eur=0,
            total_amount_eur=0,
            internal_notes=internal_notes,
            status=InvoiceStatus.INVOICE_DRAFT,
        )
        self.db.add(invoice)
        self.db.flush()

        subtotal = Decimal("0.00")
        for order in ordered:
            for box in boxes_by_order[order.id]:
                for item in box.items:
                    price = prices_by_order[order.id][(item.product_id, item.pack_size)]
                    line_total = _money(item.final_packed_count * price)
                    subtotal += line_total
                    self.db.add(
                        InvoiceLineItem(
                            invoice_id=invoice.id,
                            source_order_id=order.id,
                            product_id=item.product_id,
                            box_number=box.box_number,
                            pack_size=item.pack_size,
                            billed_pack_count=item.final_packed_count,
                            billed_weight_kg=item.final_net_weight_kg,
                            unit_price_eur=price,
                            line_total_eur=line_total,
                        )
                    )
            WorkflowService(self.db).transition(
                target=order,
                to_status=OrderStatus.INVOICES_DRAFTED,
                actor=actor,
                target_table="client_orders",
            )

        invoice.subtotal_eur = invoice.total_amount_eur = _money(subtotal)
        self._audit(
            "EVT_MASTER_INVOICE_DRAFTED",
            actor,
            "invoices",
            invoice.id,
            before=None,
            after={
                "status": InvoiceStatus.INVOICE_DRAFT.value,
                "order_ids": order_ids,
                "total_eur": float(subtotal),
            },
        )
        self.db.flush()
        self.db.refresh(invoice)
        return invoice

    def approve_and_dispatch(self, *, invoice_id: str, actor: User) -> Invoice:
        self._require_accounting(actor)
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        if invoice.status != InvoiceStatus.INVOICE_DRAFT:
            raise ValueError("Only a draft invoice can be approved and dispatched")
        invoice.status = InvoiceStatus.INVOICE_SENT
        invoice.reviewed_by_accounting = actor.id
        invoice.dispatched_at = datetime.now(UTC)
        for order in self._invoice_orders(invoice):
            WorkflowService(self.db).transition(
                target=order,
                to_status=OrderStatus.INVOICES_SENT,
                actor=actor,
                target_table="client_orders",
            )
        self._audit(
            "EVT_INVOICE_DISPATCH",
            actor,
            "invoices",
            invoice.id,
            before={"status": InvoiceStatus.INVOICE_DRAFT.value},
            after={
                "status": InvoiceStatus.INVOICE_SENT.value,
                "invoice_number": invoice.invoice_number,
                "total_eur": float(invoice.total_amount_eur),
                "recipient_customer_id": invoice.customer_id,
                "recipient_email": invoice.customer.billing_email,
            },
        )
        self.db.flush()
        self.db.refresh(invoice)
        return invoice

    def update_draft(
        self,
        *,
        invoice_id: str,
        line_prices: dict[str, float],
        internal_notes: str | None,
        actor: User,
    ) -> Invoice:
        self._require_accounting(actor)
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        if invoice.status != InvoiceStatus.INVOICE_DRAFT:
            raise ValueError("Only a draft invoice can be edited")
        if set(line_prices) != {item.id for item in invoice.items}:
            raise ValueError("Every draft line item must be included exactly once")

        before = {item.id: float(item.unit_price_eur) for item in invoice.items}
        prepared = {}
        for item in invoice.items:
            price = positive_money(line_prices[item.id])
            prepared[item.id] = (price, _money(item.billed_pack_count * price))
        subtotal = _money(sum((line[1] for line in prepared.values()), Decimal("0")))
        total = _money(subtotal + _money(invoice.vat_amount_eur or 0))
        for item in invoice.items:
            price, line_total = prepared[item.id]
            item.unit_price_eur = price
            item.line_total_eur = line_total
        invoice.internal_notes = internal_notes
        invoice.revision += 1
        invoice.subtotal_eur = _money(subtotal)
        invoice.total_amount_eur = total
        self._audit(
            "EVT_INVOICE_DRAFT_UPDATED",
            actor,
            "invoices",
            invoice.id,
            before={"line_prices": before},
            after={
                "line_prices": {item.id: float(item.unit_price_eur) for item in invoice.items},
                "total_eur": float(invoice.total_amount_eur),
            },
        )
        self.db.flush()
        self.db.refresh(invoice)
        return invoice

    def record_payment_and_issue_receipt(
        self,
        *,
        invoice_id: str,
        amount_paid: float,
        payment_date: str,
        bank_name: str,
        bank_wire_reference: str,
        payment_mode: str,
        actor: User,
        internal_notes: str | None = None,
    ) -> dict[str, Any]:
        self._require_accounting(actor)
        amount_paid = positive_money(amount_paid)
        payment_date = canonical_day(payment_date)
        bank_name = required_text(bank_name, limit=64)
        bank_wire_reference = required_text(bank_wire_reference, limit=128)
        if payment_mode not in {"BANK_TRANSFER", "CREDIT_LETTER", "CASH"}:
            raise ValueError("Unsupported payment mode")
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        if invoice.status not in {InvoiceStatus.INVOICE_SENT, InvoiceStatus.PARTIALLY_PAID}:
            raise ValueError("Invoice must be dispatched before payment can be recorded")
        duplicate = (
            self.db.query(PaymentTransaction)
            .filter(func.trim(PaymentTransaction.bank_wire_reference) == bank_wire_reference)
            .first()
        )
        if duplicate:
            raise ValueError("Bank transaction reference has already been recorded")

        total_due = _money(invoice.total_amount_eur)
        paid_now = _money(amount_paid)
        previous_paid = _money(invoice.amount_paid_eur or 0)
        outstanding_balance = total_due - previous_paid
        if paid_now > outstanding_balance:
            raise ValueError(
                f"Payment amount exceeds the outstanding balance of {outstanding_balance} {invoice.currency}"
            )
        new_total_paid = previous_paid + paid_now
        rolling_balance = max(Decimal("0.00"), total_due - new_total_paid)
        previous_status = invoice.status
        self.db.add(
            PaymentTransaction(
                invoice_id=invoice.id,
                amount_paid_eur=paid_now,
                payment_date=payment_date,
                bank_name=bank_name,
                bank_wire_reference=bank_wire_reference,
                payment_mode=payment_mode,
                rolling_balance_remaining_eur=rolling_balance,
                internal_notes=internal_notes,
                recorded_by=actor.id,
            )
        )
        invoice.amount_paid_eur = new_total_paid
        invoice.bank_wire_reference = bank_wire_reference
        invoice.payment_cleared_by = actor.id
        invoice.payment_cleared_at = datetime.now(UTC)
        invoice.status = (
            InvoiceStatus.CONFIRMED_PAID if rolling_balance == 0 else InvoiceStatus.PARTIALLY_PAID
        )
        self._audit(
            "EVT_PAYMENT_CONFIRMED",
            actor,
            "invoices",
            invoice.id,
            before={"status": previous_status.value, "paid_eur": float(previous_paid)},
            after={
                "status": invoice.status.value,
                "paid_eur": float(new_total_paid),
                "balance_eur": float(rolling_balance),
                "payment_mode": payment_mode,
                "bank_reference": bank_wire_reference,
            },
        )

        receipt = None
        if rolling_balance == 0:
            orders = self._invoice_orders(invoice)
            for order in orders:
                WorkflowService(self.db).transition(
                    target=order,
                    to_status=OrderStatus.CONFIRMED_PAID,
                    actor=actor,
                    target_table="client_orders",
                )
            receipt = self._issue_receipt(invoice, payment_date, bank_wire_reference)
            for order in orders:
                WorkflowService(self.db).transition(
                    target=order,
                    to_status=OrderStatus.RECEIPTS_ISSUED,
                    actor=actor,
                    target_table="client_orders",
                )

        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("Bank transaction reference has already been recorded") from exc
        return {
            "invoice_number": invoice.invoice_number,
            "currency": invoice.currency,
            "total_due_eur": float(total_due),
            "amount_paid_this_tx": float(paid_now),
            "total_amount_paid_eur": float(new_total_paid),
            "rolling_balance_remaining_eur": float(rolling_balance),
            "status": invoice.status.value,
            "receipt_issued": receipt is not None,
            "receipt_number": receipt.receipt_number if receipt else None,
        }

    def _issue_receipt(
        self,
        invoice: Invoice,
        payment_date: str,
        bank_reference: str,
    ) -> Receipt:
        existing = self.db.query(Receipt).filter_by(invoice_id=invoice.id).first()
        if existing:
            return existing
        receipt_number = f"RCP-{payment_date.replace('-', '')}-{invoice.id[:8]}"
        issuer = (
            self.db.query(User)
            .filter(
                User.role == UserRole.AI_AGENT_1,
                User.is_service_principal.is_(True),
                User.is_active.is_(True),
            )
            .first()
        )
        if not issuer:
            raise ValueError("AI Agent 1 service principal is required to issue receipts")
        if not invoice.seller_customer or not invoice.seller_customer.tax_vat_id:
            raise ValueError("Seller tax ID is required to issue an official receipt")
        if not invoice.customer or not invoice.customer.tax_vat_id:
            raise ValueError("Buyer tax ID is required to issue an official receipt")
        payment_ledger = [
            {
                "bank_reference": transaction.bank_wire_reference,
                "amount_eur": float(transaction.amount_paid_eur),
            }
            for transaction in invoice.transactions
        ]
        payload = {
            "receipt_number": receipt_number,
            "currency": invoice.currency,
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer_id,
            "amount_received_eur": float(invoice.amount_paid_eur),
            "bank_reference": bank_reference,
            "payment_ledger": payment_ledger,
            "seller_tax_id": invoice.seller_customer.tax_vat_id,
            "buyer_tax_id": invoice.customer.tax_vat_id,
        }
        receipt = Receipt(
            receipt_number=receipt_number,
            invoice_id=invoice.id,
            customer_id=invoice.customer_id,
            amount_received_eur=invoice.amount_paid_eur,
            bank_reference=bank_reference,
            receipt_pdf_url=f"/api/v3/invoicing/{invoice.id}/receipt",
            payload_sha256=canonical_sha256(payload),
            issued_by_agent="AI_AGENT_1",
        )
        self.db.add(receipt)
        self.db.flush()
        self._audit(
            "EVT_RECEIPT_ISSUED",
            issuer,
            "receipts",
            receipt.id,
            before=None,
            after={**payload, "payload_sha256": receipt.payload_sha256},
        )
        return receipt

    def close_paid_order(self, *, invoice_id: str, actor: User) -> ClientOrder:
        """Close a financially settled order after verifying its receipt invariant."""
        self._require_accounting(actor)
        invoice = self.db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        if invoice.status != InvoiceStatus.CONFIRMED_PAID:
            raise ValueError("Only a fully paid invoice can close an order")
        receipt = self.db.query(Receipt).filter_by(invoice_id=invoice.id).first()
        if not receipt or not receipt.payload_sha256:
            raise ValueError("A signed receipt is required before order closure")
        orders = self._invoice_orders(invoice, for_update=True)
        if any(order.status != OrderStatus.RECEIPTS_ISSUED for order in orders):
            raise ValueError("Order must have an issued receipt before closure")
        for order in orders:
            WorkflowService(self.db).transition(
                target=order,
                to_status=OrderStatus.ORDER_CLOSED,
                actor=actor,
                target_table="client_orders",
            )
        self.db.flush()
        self.db.refresh(orders[0])
        return orders[0]

    def _invoice_orders(self, invoice: Invoice, *, for_update: bool = False) -> list[ClientOrder]:
        order_ids = {item.source_order_id for item in invoice.items if item.source_order_id}
        order_ids.add(invoice.client_order_id)
        query = self.db.query(ClientOrder).filter(ClientOrder.id.in_(order_ids))
        return (query.with_for_update() if for_update else query).all()

    @staticmethod
    def _assert_packing_matches_handoff(
        boxes: list[Box],
        handoff: AgentHandoff,
    ) -> None:
        signed_boxes = {
            signed_box["box_uuid"]: signed_box
            for signed_box in handoff.payload_json.get("boxes", [])
        }
        if {box.id for box in boxes} != set(signed_boxes):
            raise ValueError("Current packing records do not match signed Handoff 02")
        for box in boxes:
            signed_box = signed_boxes[box.id]
            for key, current in [("packing_mode", box.packing_mode),
                                 ("packing_snapshot", box.packing_snapshot),
                                 ("shipping_dimensions_cm", box.shipping_dimensions_snapshot)]:
                if key in signed_box and signed_box[key] != current:
                    raise ValueError("Locked packing rules differ from signed Handoff 02")
            current_header = (
                box.box_number,
                round(float(box.actual_gross_weight_kg), 2),
                round(float(box.actual_net_weight_kg), 2),
            )
            signed_header = (
                signed_box["box_number"],
                round(float(signed_box["actual_gross_weight_kg"]), 2),
                round(float(signed_box["actual_net_weight_kg"]), 2),
            )
            if current_header != signed_header:
                raise ValueError("Current packing records do not match signed Handoff 02")
            current_items = sorted(
                (
                    item.product_id,
                    item.pack_size,
                    item.final_packed_count,
                    round(float(item.final_net_weight_kg), 2),
                )
                for item in box.items
            )
            signed_items = sorted(
                (
                    item["product_id"],
                    item["pack_size"],
                    item["final_pack_count"],
                    round(float(item["final_net_weight_kg"]), 2),
                )
                for item in signed_box.get("items", [])
            )
            if current_items != signed_items:
                raise ValueError("Current packing records do not match signed Handoff 02")
            validate_verified_net(box)

    @staticmethod
    def _require_accounting(actor: User) -> None:
        require(actor, Action.MANAGE_BILLING)

    def _audit(
        self,
        event_type: str,
        actor: User,
        target_table: str,
        target_id: str,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            AuditLog(
                event_type=event_type,
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table=target_table,
                target_id=target_id,
                before_state=before,
                after_state=after,
            )
        )
