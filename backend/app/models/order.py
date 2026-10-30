import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import deferred, relationship

from app.core.database import Base, uuid_pk


class OrderStatus(str, enum.Enum):  # noqa: UP042 -- Preserve legacy enum string behavior.
    UPLOADED = "UPLOADED"
    PARSED = "PARSED"
    SPLIT_AND_VALIDATED = "SPLIT_AND_VALIDATED"
    PURCHASING_CONSOLIDATED = "PURCHASING_CONSOLIDATED"
    PROCUREMENT_CONFIRMED = "PROCUREMENT_CONFIRMED"
    HANDOFF_01_SENT = "HANDOFF_01_SENT"
    PACKING_DESIGNED = "PACKING_DESIGNED"
    FLOOR_PACKING = "FLOOR_PACKING"
    FINAL_PACK_LOCKED = "FINAL_PACK_LOCKED"
    HANDOFF_02_RETURNED = "HANDOFF_02_RETURNED"
    INVOICES_DRAFTED = "INVOICES_DRAFTED"
    INVOICES_SENT = "INVOICES_SENT"
    CONFIRMED_PAID = "CONFIRMED_PAID"
    RECEIPTS_ISSUED = "RECEIPTS_ISSUED"
    ORDER_CLOSED = "ORDER_CLOSED"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"


class RawOrderFile(Base):
    __tablename__ = "raw_order_files"
    __table_args__ = (CheckConstraint(
        "(file_sha256 IS NULL AND file_size_bytes IS NULL) OR "
        "(file_sha256 IS NOT NULL AND length(file_sha256) = 64 AND file_size_bytes IS NOT NULL AND file_size_bytes > 0 AND file_size_bytes <= 26214400)",
        name="ck_raw_file_integrity_metadata"),)

    id = Column(String(36), primary_key=True, default=uuid_pk)
    file_name = Column(String(256), nullable=False)
    storage_url = Column(String(512), nullable=False, index=True)
    file_sha256 = Column(String(64), nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    shipment_target_date = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    route_line = Column(String(32), nullable=False)
    uploaded_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    main_customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True)
    detected_sub_customer_count = Column(Integer, default=0)
    acknowledgment_summary = deferred(Column(
        JSON, nullable=True
    ))  # Explicit review reads only; summaries must not fetch full document JSON.
    status = Column(Enum(OrderStatus), default=OrderStatus.UPLOADED)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    client_orders = relationship(
        "ClientOrder", back_populates="raw_file", cascade="all, delete-orphan"
    )
    main_customer = relationship("Customer", foreign_keys=[main_customer_id])


class ClientOrder(Base):
    __tablename__ = "client_orders"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    packing_revision = Column(BigInteger, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": packing_revision}
    raw_order_file_id = Column(String(36), ForeignKey("raw_order_files.id"), nullable=False)
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)  # Sub-Customer
    main_customer_id = Column(
        String(36), ForeignKey("customers.id"), nullable=True
    )  # Main Customer (e.g. Tonio)
    order_ref_id = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 'ORD-YYYYMMDD-CUST-XXX'
    total_ordered_items_count = Column(Integer, nullable=False, default=0)
    total_estimated_net_weight_kg = Column(Numeric(12, 4), nullable=False, default=0.00)
    status = Column(Enum(OrderStatus), default=OrderStatus.SPLIT_AND_VALIDATED)
    packing_approval_status = Column(
        String(16), nullable=False, default="NONE", server_default="NONE"
    )
    packing_requested_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    packing_requested_at = Column(DateTime(timezone=True), nullable=True)
    packing_review_note = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    raw_file = relationship("RawOrderFile", back_populates="client_orders")
    customer = relationship("Customer", foreign_keys=[customer_id])
    main_customer = relationship("Customer", foreign_keys=[main_customer_id])
    items = relationship(
        "ClientOrderItem", back_populates="client_order", cascade="all, delete-orphan"
    )


class ClientOrderItem(Base):
    __tablename__ = "client_order_items"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    client_order_id = Column(String(36), ForeignKey("client_orders.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    replacement_product_id = Column(
        String(36), ForeignKey("products.id", name="fk_order_item_replacement_product"), nullable=True
    )
    raw_produce_text = Column(String(256), nullable=False)
    pack_size = Column(String(64), nullable=False)
    ordered_pack_count = Column(Integer, nullable=False)
    confirmed_pack_count = Column(Integer, nullable=True)
    unit_price_eur = Column(Numeric(10, 2), nullable=True)
    estimated_net_weight_kg = Column(Numeric(12, 4), nullable=False)
    confirmed_net_weight_kg = Column(Numeric(12, 4), nullable=True)
    quantity_source = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    client_order = relationship("ClientOrder", back_populates="items")
    product = relationship("Product", foreign_keys=[product_id])
    replacement_product = relationship("Product", foreign_keys=[replacement_product_id])

    @property
    def packing_product(self):
        """Actual approved goods; product_id continues to describe the original order."""
        return self.replacement_product or self.product
