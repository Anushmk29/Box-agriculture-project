import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import deferred, relationship

from app.core.database import Base, uuid_pk


class InvoiceStatus(enum.StrEnum):
    INVOICE_DRAFT = "INVOICE_DRAFT"
    INVOICE_SENT = "INVOICE_SENT"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    CONFIRMED_PAID = "CONFIRMED_PAID"
    CANCELLED = "CANCELLED"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    revision = Column(BigInteger, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": revision}
    invoice_number = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 'INV-YYYYMMDD-XXXX'
    client_order_id = Column(String(36), ForeignKey("client_orders.id"), nullable=False)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)
    final_packing_manifest_id = Column(
        String(64), nullable=False
    )  # Cryptographic link to Handoff 02
    subtotal_eur = Column(Numeric(10, 2), nullable=False)
    vat_rate = Column(Numeric(4, 2), default=0.00)
    vat_amount_eur = Column(Numeric(10, 2), default=0.00)
    total_amount_eur = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(8), default="THB")
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.INVOICE_DRAFT)
    pdf_storage_url = Column(String(512), nullable=True)
    reviewed_by_accounting = Column(String(36), ForeignKey("users.id"), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    payment_cleared_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    payment_cleared_at = Column(DateTime(timezone=True), nullable=True)
    bank_wire_reference = Column(String(128), nullable=True)
    amount_paid_eur = Column(Numeric(10, 2), default=0.00)
    invoice_type = Column(
        String(32), default="ON_BEHALF_SUB_CUSTOMER"
    )  # 'SUPPLIER_DIRECT', 'ON_BEHALF_SUB_CUSTOMER', 'MASTER_SUMMARY'
    seller_customer_id = Column(
        String(36), ForeignKey("customers.id"), nullable=True
    )  # Main customer when on-behalf
    internal_notes = deferred(Column(
        Text, nullable=True
    ))  # Accounting/Agent1 draft detail; omitted from customer-facing invoice HTML.
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")
    customer = relationship("Customer", foreign_keys=[customer_id])
    seller_customer = relationship("Customer", foreign_keys=[seller_customer_id])
    receipt = relationship("Receipt", back_populates="invoice", uselist=False)
    transactions = relationship(
        "PaymentTransaction", back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False)
    source_order_id = Column(String(36), ForeignKey("client_orders.id"), nullable=True, index=True)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    box_number = Column(Integer, nullable=False)
    pack_size = Column(String(64), nullable=False)
    billed_pack_count = Column(Integer, nullable=False)
    billed_weight_kg = Column(
        Numeric(10, 4), nullable=False
    )  # Locked final packed item weight
    unit_price_eur = Column(Numeric(10, 2), nullable=False)
    line_total_eur = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product")
    source_order = relationship("ClientOrder")


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    receipt_number = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 'RCP-YYYYMMDD-XXXX'
    invoice_id = Column(String(36), ForeignKey("invoices.id"), unique=True, nullable=False)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)
    amount_received_eur = Column(Numeric(10, 2), nullable=False)
    bank_reference = Column(String(128), nullable=False)
    receipt_pdf_url = Column(String(512), nullable=False)
    payload_sha256 = Column(String(64), nullable=True)
    issued_by_agent = Column(String(32), default="AI_AGENT_1")
    internal_notes = Column(Text, nullable=True)  # Private text field on generated receipt
    issued_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    invoice = relationship("Invoice", back_populates="receipt")
    customer = relationship("Customer")


class PaymentTransaction(Base):
    """Sub-ledger for partial payments, bank clearances, and rolling balances"""

    __tablename__ = "payment_transactions"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False)
    amount_paid_eur = Column(Numeric(10, 2), nullable=False)
    payment_date = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    bank_name = Column(String(64), nullable=False)
    bank_wire_reference = Column(String(128), unique=True, nullable=False, index=True)
    payment_mode = Column(String(32), nullable=True)
    rolling_balance_remaining_eur = Column(Numeric(10, 2), nullable=False)
    internal_notes = Column(Text, nullable=True)  # Private memo for owners/admins
    recorded_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    invoice = relationship("Invoice", back_populates="transactions")
