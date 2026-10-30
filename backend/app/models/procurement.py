from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base, uuid_pk
from app.models.order import OrderStatus


class MasterPurchasingList(Base):
    __tablename__ = "master_purchasing_lists"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    raw_order_file_id = Column(
        String(36), ForeignKey("raw_order_files.id"), nullable=True
    )  # Nullable for combined batches
    batch_reference = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 'PUR-YYYYMMDD-BATCH1'
    shipment_date = Column(String(10), nullable=True)
    route_code = Column(String(32), nullable=True)
    total_skus_count = Column(Integer, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PURCHASING_CONSOLIDATED)
    confirmed_by_buyer = Column(String(36), ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    items = relationship(
        "PurchasingItem", back_populates="purchasing_list", cascade="all, delete-orphan"
    )
    sources = relationship(
        "PurchasingOrderSource", back_populates="purchasing_list", cascade="all, delete-orphan"
    )


class PurchasingOrderSource(Base):
    """Junction table recording source files and client orders combined into a Master Purchase Order"""

    __tablename__ = "purchasing_order_sources"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    purchasing_list_id = Column(
        String(36), ForeignKey("master_purchasing_lists.id"), nullable=False
    )
    raw_order_file_id = Column(String(36), ForeignKey("raw_order_files.id"), nullable=True)
    client_order_id = Column(String(36), ForeignKey("client_orders.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    purchasing_list = relationship("MasterPurchasingList", back_populates="sources")
    raw_order_file = relationship("RawOrderFile")
    client_order = relationship("ClientOrder")


class PurchasingItem(Base):
    __tablename__ = "purchasing_items"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    purchasing_list_id = Column(
        String(36), ForeignKey("master_purchasing_lists.id"), nullable=False
    )
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    pack_size = Column(String(64), nullable=False)
    ordered_net_weight_kg = Column(
        Numeric(12, 4), nullable=False, default=0.00
    )  # Base customer order weight
    buffer_percentage = Column(Numeric(5, 2), default=0.00)  # Buffer % added for trimming/yield
    buffer_weight_kg = Column(Numeric(12, 4), default=0.00)  # Calculated buffer kg
    total_required_pack_count = Column(
        Integer, nullable=False
    )  # Final purchasing packs with buffer
    total_required_weight_kg = Column(
        Numeric(12, 4), nullable=False
    )  # Final purchasing weight = ordered + buffer
    confirmed_purchased_pack_count = Column(Integer, nullable=True)
    confirmed_purchased_weight_kg = Column(Numeric(12, 4), nullable=True)
    shortage_reason = Column(Text, nullable=True)
    replacement_product_id = Column(String(36), ForeignKey("products.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    purchasing_list = relationship("MasterPurchasingList", back_populates="items")
    product = relationship("Product", foreign_keys=[product_id])
    replacement_product = relationship("Product", foreign_keys=[replacement_product_id])
