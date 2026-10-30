from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dates import canonical_day
from app.core.dependencies import require_access
from app.core.values import PackLabel, PositiveMoney, UnicodeText
from app.models.audit import AuditLog
from app.models.master_data import ClientSkuMapping, Customer, Product
from app.models.user import User, UserRole
from app.modules.pricing import PriceCatalogue

router = APIRouter(
    prefix="/master-data/sku-mappings",
    tags=["Master Data: SKU Standardization & Selling Prices"],
    dependencies=[Depends(require_access(UserRole.GENERAL))],
)


class SkuMappingCreate(BaseModel):
    main_customer_id: str | None = None
    client_sku_code: str | None = None
    client_raw_name: UnicodeText = Field(min_length=1, max_length=256)
    product_id: str
    standard_pack_size: PackLabel | None = "100g"


class SkuMappingOut(BaseModel):
    id: str
    main_customer_id: str | None
    client_sku_code: str | None
    client_raw_name: str
    product_id: str
    product_name_vi: str
    product_name_en: str
    standard_pack_size: str | None
    is_verified: bool


class SellingPriceCreate(BaseModel):
    main_customer_id: str
    sub_customer_id: str | None = None  # NULL = applies to all sub-customers of this main customer
    product_id: str
    pack_size: PackLabel
    selling_price_eur: PositiveMoney
    valid_from: str = Field(default_factory=lambda: date.today().isoformat())
    valid_to: None = None

    @field_validator("valid_from")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return canonical_day(value)

    @field_validator("valid_to", mode="before")
    @classmethod
    def reject_expiry(cls, value):
        if value is not None:
            raise ValueError("Temporary prices and expiry dates are not supported; prices remain until admin or finance changes them")
        return None


class SellingPriceOut(BaseModel):
    id: str
    main_customer_id: str
    sub_customer_id: str | None
    product_id: str
    pack_size: str
    selling_price_eur: float
    valid_from: str
    valid_to: str | None


@router.post("", response_model=SkuMappingOut)
def create_or_update_sku_mapping(
    payload: SkuMappingCreate,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(
        require_access(UserRole.GENERAL)
    ),
):
    """
    SKU Standardization:
    Pre-feed client SKU database list to eliminate duplicate entries and typos.
    """
    product = db.query(Product).filter(Product.id == payload.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {payload.product_id} not found")
    if payload.main_customer_id:
        main_customer = (
            db.query(Customer)
            .filter(
                Customer.id == payload.main_customer_id,
                Customer.is_main_customer.is_(True),
            )
            .first()
        )
        if not main_customer:
            raise HTTPException(status_code=404, detail="Main customer not found")

    existing = (
        db.query(ClientSkuMapping)
        .filter(
            ClientSkuMapping.main_customer_id == payload.main_customer_id,
            ClientSkuMapping.client_raw_name == payload.client_raw_name.strip(),
        )
        .first()
    )

    before = None
    if existing:
        before = {
            "client_sku_code": existing.client_sku_code,
            "client_raw_name": existing.client_raw_name,
            "product_id": existing.product_id,
            "standard_pack_size": existing.standard_pack_size,
        }
        existing.client_sku_code = (
            payload.client_sku_code.strip().upper() if payload.client_sku_code else None
        )
        existing.product_id = product.id
        existing.standard_pack_size = payload.standard_pack_size
        mapping = existing
    else:
        mapping = ClientSkuMapping(
            main_customer_id=payload.main_customer_id,
            client_sku_code=payload.client_sku_code.strip().upper()
            if payload.client_sku_code
            else None,
            client_raw_name=payload.client_raw_name.strip(),
            product_id=product.id,
            standard_pack_size=payload.standard_pack_size,
            is_verified=True,
        )
        db.add(mapping)

    db.flush()
    db.add(
        AuditLog(
            event_type="SKU_MAPPING_UPSERTED",
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="client_sku_mappings",
            target_id=mapping.id,
            before_state=before,
            after_state={
                "main_customer_id": mapping.main_customer_id,
                "client_sku_code": mapping.client_sku_code,
                "client_raw_name": mapping.client_raw_name,
                "product_id": mapping.product_id,
                "standard_pack_size": mapping.standard_pack_size,
            },
        )
    )
    db.flush()
    db.refresh(mapping)

    return SkuMappingOut(
        id=mapping.id,
        main_customer_id=mapping.main_customer_id,
        client_sku_code=mapping.client_sku_code,
        client_raw_name=mapping.client_raw_name,
        product_id=product.id,
        product_name_vi=product.name_vi,
        product_name_en=product.name_en,
        standard_pack_size=mapping.standard_pack_size,
        is_verified=mapping.is_verified,
    )


@router.get("", response_model=list[SkuMappingOut])
def list_sku_mappings(main_customer_id: str | None = None, db: Session = Depends(get_db, scope="function")):
    query = db.query(ClientSkuMapping)
    if main_customer_id:
        query = query.filter(
            (ClientSkuMapping.main_customer_id == main_customer_id)
            | (ClientSkuMapping.main_customer_id.is_(None))
        )
    mappings = query.all()

    return [
        SkuMappingOut(
            id=m.id,
            main_customer_id=m.main_customer_id,
            client_sku_code=m.client_sku_code,
            client_raw_name=m.client_raw_name,
            product_id=m.product_id,
            product_name_vi=m.product.name_vi if m.product else "",
            product_name_en=m.product.name_en if m.product else "",
            standard_pack_size=m.standard_pack_size,
            is_verified=m.is_verified,
        )
        for m in mappings
    ]


@router.post("/selling-prices", response_model=SellingPriceOut)
def set_main_customer_selling_price(
    payload: SellingPriceCreate,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_access(UserRole.GENERAL_ACCOUNTING)),
):
    """
    On-Behalf Invoicing Database:
    Configures the main customer's designated selling prices for their sub-customers.
    """
    try:
        price_entry = PriceCatalogue(db).set_price(
            actor=actor,
            main_customer_id=payload.main_customer_id,
            sub_customer_id=payload.sub_customer_id,
            product_id=payload.product_id,
            pack_size=payload.pack_size,
            price=payload.selling_price_eur,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SellingPriceOut(
        id=price_entry.id,
        main_customer_id=price_entry.main_customer_id,
        sub_customer_id=price_entry.sub_customer_id,
        product_id=price_entry.product_id,
        pack_size=price_entry.pack_size,
        selling_price_eur=float(price_entry.selling_price_eur),
        valid_from=price_entry.valid_from,
        valid_to=price_entry.valid_to,
    )
