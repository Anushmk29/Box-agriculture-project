import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_access
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

router = APIRouter(
    prefix="/master-data",
    tags=["Master Data"],
    dependencies=[Depends(require_access(UserRole.GENERAL))],
)


class ProductCreate(BaseModel):
    t_code: str = Field(min_length=1, max_length=32)
    numeric_code: str | None = None
    name_vi: str | None = None
    name_en: str | None = None
    name_th: str | None = None
    produce_type: ProduceType = ProduceType.LEAFY
    box_material: BoxMaterial = BoxMaterial.FOAM
    temperature: TemperatureZone = TemperatureZone.COLD
    control_status: ControlStatus = ControlStatus.COMBINABLE
    weight_class: WeightClass = WeightClass.LIGHT
    default_pack_sizes: list[str] = Field(default_factory=lambda: ["100g", "200g"])
    default_unit_volume_cm3: float = 1000.0
    tare_weight_allowance_g: float = 15.0
    default_buffer_percentage: float = 0.0
    botanical_family: str | None = None
    barcode: str | None = None
    norway_code: str | None = None


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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
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
    db.commit()
    db.refresh(product)
    return product


@router.get("/boxes", response_model=list[BoxOut])
def list_boxes(db: Session = Depends(get_db)):
    return db.query(BoxCatalogue).filter(BoxCatalogue.is_active.is_(True)).all()


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    route: str | None = None,
    main_only: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Customer)
    if route:
        q = q.filter(Customer.route_group == route)
    if main_only:
        q = q.filter(Customer.is_main_customer.is_(True))
    return q.order_by(Customer.client_code).all()
