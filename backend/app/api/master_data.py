import unicodedata
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_access, require_action
from app.core.policy import Action, require
from app.core.values import MONEY_MAX, PackLabel, UnicodeText
from app.models.master_data import (
    BoxCatalogue,
    BoxMaterial,
    ControlStatus,
    Customer,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.user import User, UserRole
from app.modules.orders.reconciliation import merge_products
from app.modules.packing.catalogue import reference_for
from app.services.order_customer_reference import order_routes

router = APIRouter(
    prefix="/master-data",
    tags=["Master Data"],
    dependencies=[Depends(require_access(UserRole.GENERAL))],
)


class ProductCreate(BaseModel):
    t_code: UnicodeText = Field(min_length=1, max_length=32)
    numeric_code: UnicodeText | None = Field(default=None, max_length=32)
    name_vi: UnicodeText | None = Field(default=None, max_length=256)
    name_en: UnicodeText | None = Field(default=None, max_length=256)
    name_th: UnicodeText | None = Field(default=None, max_length=256)
    produce_type: ProduceType = ProduceType.OTHER
    box_material: BoxMaterial = BoxMaterial.FOAM
    temperature: TemperatureZone = TemperatureZone.COLD
    control_status: ControlStatus = ControlStatus.COMBINABLE
    weight_class: WeightClass = WeightClass.LIGHT
    default_pack_sizes: list[PackLabel] = Field(default_factory=lambda: ["100g", "200g"])
    default_unit_volume_cm3: Decimal = Field(default=Decimal("1000"), gt=0, le=MONEY_MAX,
                                            max_digits=10, decimal_places=2, allow_inf_nan=False)
    tare_weight_allowance_g: Decimal = Field(default=Decimal("15"), ge=0, le=Decimal("999999.99"),
                                            max_digits=8, decimal_places=2, allow_inf_nan=False)
    default_buffer_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100,
                                               max_digits=5, decimal_places=2, allow_inf_nan=False)
    botanical_family: UnicodeText | None = Field(default=None, max_length=128)
    barcode: UnicodeText | None = Field(default=None, max_length=32)
    norway_code: UnicodeText | None = Field(default=None, max_length=32)

    @field_validator("t_code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    t_code: str
    numeric_code: str | None = None
    name_vi: str
    name_en: str
    name_th: str
    box_material: str
    temperature: str
    control_status: str
    produce_type: str | None = None
    weight_class: str | None = None
    default_pack_sizes: list[str] | None = None


class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    box_type: str
    size_name: str
    length_cm: float
    width_cm: float
    height_cm: float
    rated_weight_kg: float
    volume_m3: float


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_code: str
    company_name: str
    route_group: str
    destination_country: str
    is_main_customer: bool


class MainCustomerCreate(BaseModel):
    client_code: UnicodeText = Field(min_length=1, max_length=64)
    company_name: UnicodeText = Field(min_length=1, max_length=256)
    route_group: UnicodeText = Field(min_length=1, max_length=32)
    destination_country: UnicodeText = Field(min_length=1, max_length=64)
    billing_email: UnicodeText = Field(default="", max_length=255)

    @field_validator("client_code", mode="before")
    @classmethod
    def normalize_client_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("client_code", "company_name", "route_group", "destination_country")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_main_customer(
    payload: MainCustomerCreate,
    db: Session = Depends(get_db, scope="function"),
    _actor: User = Depends(require_action(Action.VALIDATE_ORDERS)),
):
    code = payload.client_code.upper()
    if db.query(Customer).filter(func.upper(Customer.client_code) == code).first():
        raise HTTPException(409, "Customer code already exists. Select the existing account or use a different code.")
    customer = Customer(
        client_code=code,
        company_name=payload.company_name,
        route_group=payload.route_group,
        destination_country=payload.destination_country,
        billing_email=payload.billing_email.strip(),
        is_main_customer=True,
    )
    db.add(customer)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Customer code already exists. Select the existing account or use a different code.") from exc
    db.refresh(customer)
    return customer


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


@router.get("/products", response_model=list[ProductOut])
def list_products(
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db, scope="function"),
):
    query = db.query(Product).filter(Product.is_active.is_(True))
    if q and q.strip():
        raw_term = q.strip()
        terms = [raw_term]
        unaccented = _strip_accents(raw_term)
        if unaccented.casefold() != raw_term.casefold():
            terms.append(unaccented)

        conditions = []
        for term in terms:
            pattern = f"%{term}%"
            conditions.extend([
                Product.t_code.ilike(pattern),
                Product.numeric_code.ilike(pattern),
                Product.barcode.ilike(pattern),
                Product.norway_code.ilike(pattern),
                Product.name_vi.ilike(pattern),
                Product.name_en.ilike(pattern),
                Product.name_th.ilike(pattern),
            ])

        clean_code = "".join(c for c in raw_term if c.isalnum())
        # If term looks like a 4-digit code e.g. "1109", also search with formatted e.g. "11-009"
        if len(clean_code) == 4 and clean_code.isdigit():
            formatted_dash = f"{clean_code[:2]}-0{clean_code[2:]}"
            conditions.append(Product.t_code.ilike(f"%{formatted_dash}%"))
            conditions.append(Product.numeric_code.ilike(f"%{clean_code[:2]}0{clean_code[2:]}%"))

        # Split into tokens for multi-word queries e.g. "Pak Kwan Tung"
        tokens = [t for t in raw_term.split() if len(t) > 1]
        for token in tokens:
            tok_pattern = f"%{token}%"
            conditions.extend([
                Product.name_en.ilike(tok_pattern),
                Product.name_vi.ilike(tok_pattern),
                Product.name_th.ilike(tok_pattern),
            ])

        query = query.filter(or_(*conditions))
    return query.order_by(Product.t_code).offset(offset).limit(limit).all()


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(get_current_user),
):
    if payload.default_buffer_percentage:
        require(actor, Action.EDIT_BUFFERS)
    clean_code = payload.t_code.strip().upper()
    if not clean_code:
        raise HTTPException(status_code=422, detail="Product code (t_code) cannot be empty")

    name_vi = (payload.name_vi or "").strip()
    name_en = (payload.name_en or "").strip()
    name_th = (payload.name_th or "").strip()

    if not name_en and not name_vi:
        raise HTTPException(
            status_code=422,
            detail="At least one produce name (English or Vietnamese) must be specified",
        )

    # Automatic multilingual fallbacks
    name_en = name_en or name_vi or clean_code
    name_vi = name_vi or name_en or clean_code
    name_th = name_th or name_en or clean_code

    existing = db.query(Product).filter(Product.t_code == clean_code).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Product with code '{clean_code}' already exists: {existing.name_en} / {existing.name_vi}.",
        )

    numeric_code = payload.numeric_code or ("".join(c for c in clean_code if c.isdigit()) or None)
    pack_sizes = [p.strip() for p in payload.default_pack_sizes if p.strip()] or ["100g"]

    product = Product(
        t_code=clean_code,
        numeric_code=numeric_code,
        name_vi=name_vi,
        name_en=name_en,
        name_th=name_th,
        box_material=payload.box_material,
        temperature=payload.temperature,
        control_status=payload.control_status,
        produce_type=payload.produce_type,
        weight_class=payload.weight_class,
        default_pack_sizes=pack_sizes,
        default_unit_volume_cm3=payload.default_unit_volume_cm3,
        tare_weight_allowance_g=payload.tare_weight_allowance_g,
        default_buffer_percentage=payload.default_buffer_percentage,
        botanical_family=payload.botanical_family,
        barcode=payload.barcode,
        norway_code=payload.norway_code,
        is_active=True,
    )
    db.add(product)
    db.flush()
    db.refresh(product)
    return product


class ProductMergeRequest(BaseModel):
    source_id: str
    target_id: str


@router.post("/products/merge")
def merge_master_products(payload: ProductMergeRequest, db: Session = Depends(get_db, scope="function"),
                         actor: User = Depends(require_action(Action.MERGE_PRODUCTS))):
    try:
        return merge_products(db, actor=actor, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/boxes", response_model=list[BoxOut])
def list_boxes(db: Session = Depends(get_db, scope="function")):
    return [box for box in db.query(BoxCatalogue).filter(BoxCatalogue.is_active.is_(True)).all()
            if reference_for(box)]


@router.get("/order-routes", response_model=list[str])
def list_order_routes(db: Session = Depends(get_db, scope="function")):
    return order_routes(db)


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    route: str | None = None,
    main_only: bool = False,
    db: Session = Depends(get_db, scope="function"),
):
    q = db.query(Customer)
    if route:
        q = q.filter(Customer.route_group == route)
    if main_only:
        q = q.filter(Customer.is_main_customer.is_(True))
    return q.order_by(Customer.client_code).all()
