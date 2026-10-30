import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base, uuid_pk


class AdjustmentType(str, enum.Enum):
    AS_ORDERED = "AS_ORDERED"
    SHORTAGE_REDUCED = "SHORTAGE_REDUCED"
    QUANTITY_INCREASED = "QUANTITY_INCREASED"
    SUBSTITUTED = "SUBSTITUTED"


class Box(Base):
    __tablename__ = "boxes"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    client_order_id = Column(String(36), ForeignKey("client_orders.id"), nullable=False)
    box_catalogue_id = Column(String(36), ForeignKey("box_catalogue.id"), nullable=False)
    box_number = Column(Integer, nullable=False)
    shipment_date = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    route_code = Column(String(32), nullable=False)
    packing_mode = Column(String(16), nullable=True)
    packing_snapshot = Column(JSON, nullable=True)
    shipping_dimensions_snapshot = Column(JSON, nullable=True)
    is_mixed_box = Column(Boolean, default=False)
    qr_code_uid = Column(String(128), unique=True, nullable=False, index=True)
    qr_image_url = Column(String(512), nullable=True)
    actual_gross_weight_kg = Column(Numeric(8, 2), nullable=True)
    actual_net_weight_kg = Column(Numeric(8, 2), nullable=True)
    weight_utilization_pct = Column(Numeric(10, 2), nullable=True)
    packed_by_staff = Column(String(36), ForeignKey("users.id"), nullable=True)
    override_box_catalogue_id = Column(String(36), ForeignKey("box_catalogue.id"), nullable=True)
    custom_length_cm = Column(Numeric(6, 2), nullable=True)
    custom_width_cm = Column(Numeric(6, 2), nullable=True)
    custom_height_cm = Column(Numeric(6, 2), nullable=True)
    volumetric_weight_kg = Column(Numeric(8, 2), nullable=True)  # (L*W*H)/6000
    chargeable_weight_kg = Column(Numeric(8, 2), nullable=True)  # max(gross, volumetric)
    is_manually_overridden = Column(Boolean, default=False)
    override_reason = Column(Text, nullable=True)
    is_locked = Column(Boolean, default=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    items = relationship("BoxItem", back_populates="box", cascade="all, delete-orphan")
    box_type = relationship("BoxCatalogue", foreign_keys=[box_catalogue_id])
    override_box_type = relationship("BoxCatalogue", foreign_keys=[override_box_catalogue_id])
    order = relationship("ClientOrder")


class BoxItem(Base):
    __tablename__ = "box_items"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    box_id = Column(String(36), ForeignKey("boxes.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    pack_size = Column(String(64), nullable=False)
    final_packed_count = Column(Integer, nullable=False)
    final_net_weight_kg = Column(Numeric(10, 4), nullable=False)
    adjustment_type = Column(Enum(AdjustmentType), default=AdjustmentType.AS_ORDERED)
    adjustment_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    box = relationship("Box", back_populates="items")
    product = relationship("Product")


class TransportationManifest(Base):
    __tablename__ = "transportation_manifests"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    manifest_reference = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 'TRN-YYYYMMDD-ROUTE'
    route_code = Column(String(32), nullable=False, index=True)
    shipment_date = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    flight_carrier = Column(String(64), nullable=True)
    total_boxes_count = Column(Integer, default=0)
    total_gross_weight_kg = Column(Numeric(10, 2), default=0.00)
    total_net_weight_kg = Column(Numeric(10, 2), default=0.00)
    total_volumetric_weight_kg = Column(Numeric(10, 2), default=0.00)
    total_chargeable_weight_kg = Column(Numeric(10, 2), default=0.00)
    total_cbm = Column(Numeric(10, 4), default=0.0000)
    status = Column(String(32), default="DRAFT")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
