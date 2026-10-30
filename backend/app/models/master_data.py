import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import backref, relationship

from app.core.database import Base, uuid_pk


class BoxMaterial(str, enum.Enum):
    FOAM = "FOAM"
    CARDBOARD = "CARDBOARD"


class TemperatureZone(str, enum.Enum):
    COLD = "COLD"
    NORMAL = "NORMAL"


class ControlStatus(str, enum.Enum):
    EL = "EL"
    COMBINABLE = "COMBINABLE"


class ProduceType(str, enum.Enum):
    LEAFY = "LEAFY"
    FRUIT = "FRUIT"
    HERB = "HERB"
    ROOT = "ROOT"
    VEGETABLE = "VEGETABLE"
    SPICE = "SPICE"
    PACKAGED_FOOD = "PACKAGED_FOOD"
    PERSONAL_CARE = "PERSONAL_CARE"
    OTHER = "OTHER"


class WeightClass(str, enum.Enum):
    LIGHT = "LIGHT"
    HEAVY = "HEAVY"


class Firmness(enum.StrEnum):
    SOFT = "SOFT"
    SEMI_HARD = "SEMI_HARD"
    HARD = "HARD"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("foam_below_kg IS NULL OR (foam_below_kg > 0 AND foam_below_kg <= 10000)", name="ck_product_foam_cutoff"),)

    id = Column(String(36), primary_key=True, default=uuid_pk)
    t_code = Column(String(32), unique=True, nullable=False, index=True)
    numeric_code = Column(String(32), nullable=True, index=True)
    name_vi = Column(String(256), nullable=False, index=True)
    name_en = Column(String(256), nullable=False, index=True)
    name_th = Column(String(256), nullable=False, index=True)
    box_material = Column(Enum(BoxMaterial), nullable=False)
    foam_below_kg = Column(Numeric(12, 4), nullable=True)
    temperature = Column(Enum(TemperatureZone), nullable=False)
    control_status = Column(Enum(ControlStatus), nullable=False)
    produce_type = Column(Enum(ProduceType), nullable=False)
    weight_class = Column(Enum(WeightClass), nullable=False)
    botanical_family = Column(String(128), nullable=True, index=True)
    default_unit_volume_cm3 = Column(Numeric(10, 2), nullable=False, default=1000.00)
    firmness = Column(String(16), nullable=True)
    packing_reviewed = Column(Boolean, nullable=False, default=False, server_default="false")
    packing_revision = Column(Integer, nullable=False, default=1, server_default="1")
    packing_notes = Column(String(2048), nullable=True)
    packing_source = Column(JSON, nullable=True)
    packing_volume_l_per_kg = Column(Numeric(12, 6), nullable=True)
    packing_volume_reviewed = Column(Boolean, nullable=False, default=False, server_default="false")
    packing_volume_estimated = Column(Boolean, nullable=False, default=True, server_default="true")
    packing_profiles = relationship(
        "ProductPackProfile", back_populates="product", cascade="all, delete-orphan"
    )
    box_limits = relationship(
        "ProductBoxLimit", back_populates="product", cascade="all, delete-orphan"
    )
    default_pack_sizes = Column(
        JSON, nullable=False, default=list
    )  # List of pack sizes e.g. ['100g', '200g']
    tare_weight_allowance_g = Column(Numeric(8, 2), default=15.00)
    default_buffer_percentage = Column(Numeric(5, 2), default=0.00)  # Sticky buffer memory
    barcode = Column(String(32), nullable=True, index=True)
    norway_code = Column(String(32), nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class BoxCatalogue(Base):
    __tablename__ = "box_catalogue"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    box_type = Column(Enum(BoxMaterial), nullable=False)
    size_name = Column(String(64), nullable=False)  # Small, Flat, Giant, etc.
    length_cm = Column(Numeric(6, 2), nullable=False)
    width_cm = Column(Numeric(6, 2), nullable=False)
    height_cm = Column(Numeric(6, 2), nullable=False)
    packing_reviewed = Column(Boolean, nullable=False, default=False, server_default="false")
    packing_revision = Column(Integer, nullable=False, default=1, server_default="1")
    rated_weight_kg = Column(Numeric(6, 2), nullable=False)
    volume_m3 = Column(Numeric(12, 8), nullable=False)
    max_safe_net_weight_kg = Column(Numeric(6, 2), nullable=False)
    is_active = Column(Boolean, default=True)


class ProductPackProfile(Base):
    __tablename__ = "product_pack_profiles"
    __table_args__ = (
        UniqueConstraint("product_id", "pack_key", name="uq_product_pack_profile"),
        CheckConstraint(
            "(net_weight_kg IS NULL OR net_weight_kg > 0) AND (volume_cm3 IS NULL OR volume_cm3 > 0) AND (NOT reviewed OR (net_weight_kg IS NOT NULL AND volume_cm3 IS NOT NULL))",
            name="ck_pack_profile_positive",
        ),
    )
    id = Column(String(36), primary_key=True, default=uuid_pk)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    pack_size = Column(String(64), nullable=False)
    pack_key = Column(String(128), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    net_weight_kg = Column(Numeric(10, 4), nullable=True)
    volume_cm3 = Column(Numeric(12, 2), nullable=True)
    evidence = Column(JSON, nullable=True)
    reviewed = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    product = relationship("Product", back_populates="packing_profiles")


class ProductBoxLimit(Base):
    __tablename__ = "product_box_limits"
    __table_args__ = (
        UniqueConstraint("product_id", "box_catalogue_id", name="uq_product_box_limit"),
        CheckConstraint("max_net_weight_kg > 0", name="ck_product_box_limit_positive"),
    )
    id = Column(String(36), primary_key=True, default=uuid_pk)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    box_catalogue_id = Column(String(36), ForeignKey("box_catalogue.id"), nullable=False)
    max_net_weight_kg = Column(Numeric(8, 2), nullable=False)
    reviewed = Column(Boolean, nullable=False, default=False)
    source_note = Column(String(2048), nullable=True)
    product = relationship("Product", back_populates="box_limits")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    client_code = Column(
        String(64), unique=True, nullable=False, index=True
    )  # e.g. 'TONIO-MAIN', 'CT-Trung', 'BE-A24'
    company_name = Column(String(256), nullable=False)
    billing_email = Column(String(255), nullable=False)
    route_group = Column(String(32), nullable=False, index=True)  # 'TETE', 'NGA', 'BE', 'CT', 'PTT'
    destination_country = Column(String(64), nullable=False)
    tax_vat_id = Column(String(64), nullable=True)
    payment_terms_days = Column(Numeric(4, 0), default=7)
    is_main_customer = Column(Boolean, default=False)
    parent_customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    sub_customers = relationship("Customer", backref=backref("parent_customer", remote_side=[id]))


class PriceList(Base):
    __tablename__ = "price_lists"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False)
    customer_id = Column(
        String(36), ForeignKey("customers.id"), nullable=True
    )  # NULL = Global default price
    pack_size = Column(String(64), nullable=False)
    unit_price_eur = Column(Numeric(10, 2), nullable=False)
    valid_from = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    valid_to = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_price_scope_pack_day", product_id, customer_id, pack_size,
        func.replace(func.trim(valid_from), "-", ""), id),)


class ClientSkuMapping(Base):
    __tablename__ = "client_sku_mappings"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    main_customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True, index=True)
    client_sku_code = Column(String(64), nullable=True, index=True)
    client_raw_name = Column(String(256), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    standard_pack_size = Column(String(64), nullable=True)
    is_verified = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    product = relationship("Product")
    main_customer = relationship("Customer", foreign_keys=[main_customer_id])


class CustomerSellingPrice(Base):
    __tablename__ = "customer_selling_prices"

    id = Column(String(36), primary_key=True, default=uuid_pk)
    main_customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False, index=True)
    sub_customer_id = Column(
        String(36), ForeignKey("customers.id"), nullable=True, index=True
    )  # NULL = all sub-customers
    product_id = Column(String(36), ForeignKey("products.id"), nullable=False, index=True)
    pack_size = Column(String(64), nullable=False)
    selling_price_eur = Column(Numeric(10, 2), nullable=False)
    valid_from = Column(String(10), nullable=False)  # 'YYYY-MM-DD'
    valid_to = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_selling_scope_pack_day", main_customer_id, sub_customer_id,
        product_id, pack_size, func.replace(func.trim(valid_from), "-", ""), id),)

    product = relationship("Product")
    main_customer = relationship("Customer", foreign_keys=[main_customer_id])
    sub_customer = relationship("Customer", foreign_keys=[sub_customer_id])
