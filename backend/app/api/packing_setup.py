"""Human-maintained product measurements and workbook box capacities."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_action
from app.core.policy import Action
from app.core.values import PackLabel
from app.models.audit import AuditLog
from app.models.master_data import (
    BoxCatalogue,
    BoxMaterial,
    ControlStatus,
    Firmness,
    ProduceType,
    Product,
    ProductBoxLimit,
    ProductPackProfile,
    TemperatureZone,
    WeightClass,
)
from app.models.order import ClientOrderItem
from app.models.packing import Box
from app.models.user import User
from app.modules.packing.catalogue import (
    dimension_source,
    external_volume_cm3,
    reference_for,
    require_workbook_box,
)
from app.modules.packing.profiles import box_volume, lookup_pack_key, pack_key, profile_for
from app.modules.packing.reference import reference, source_matches, suggestions
from app.modules.packing.research import (
    pending_drafts,
    research_data,
    research_for,
    stage_drafts,
    staging_revision,
)

router = APIRouter(
    prefix="/packing-setup",
    tags=["Packing setup"],
    dependencies=[Depends(require_action(Action.FLOOR_PACKING))],
)

from app.api.product_table import router as table_router  # noqa: E402

router.include_router(table_router, prefix="")


class MeasurementInput(BaseModel):
    length_cm: float = Field(gt=0, le=10000, allow_inf_nan=False)
    width_cm: float = Field(gt=0, le=10000, allow_inf_nan=False)
    filled_height_cm: float = Field(gt=0, le=10000, allow_inf_nan=False)
    pack_count: int = Field(gt=0, le=100000)


class ProfileEvidence(BaseModel):
    weight_source: Literal["ORDER_LABEL", "MEASURED", "SUPPLIER", "MANUAL", "UNKNOWN"] = "MANUAL"
    volume_source: Literal["MEASURED", "SUPPLIER", "RESEARCH", "PROXY", "MANUAL", "UNKNOWN"] = (
        "MEASURED"
    )
    source_url: HttpUrl | None = None
    source_note: str | None = Field(default=None, max_length=4096)
    weight_note: str | None = Field(default=None, max_length=2048)
    reference_id: str | None = Field(default=None, max_length=64)
    research_rows: list[int] = Field(default_factory=list, max_length=300)
    research_version: str | None = Field(default=None, max_length=32)
    estimate_low_cm3: float | None = Field(default=None, gt=0, le=1e9, allow_inf_nan=False)
    estimate_high_cm3: float | None = Field(default=None, gt=0, le=1e9, allow_inf_nan=False)
    estimate_kind: Literal["RESEARCH", "PROXY"] | None = None
    measurement: MeasurementInput | None = None


class PackInput(BaseModel):
    pack_size: PackLabel
    aliases: list[PackLabel] = Field(
        default_factory=list, max_length=100
    )
    net_weight_kg: float | None = Field(default=None, ge=0.0001, le=10000, allow_inf_nan=False)
    volume_cm3: float | None = Field(default=None, ge=0.01, le=1_000_000_000, allow_inf_nan=False)
    reviewed: bool = False
    evidence: ProfileEvidence = Field(default_factory=ProfileEvidence)

    @model_validator(mode="after")
    def validate_review(self):
        lookup_pack_key(self.pack_size)
        for alias in self.aliases:
            lookup_pack_key(alias)
        measurement = self.evidence.measurement
        if measurement:
            self.volume_cm3 = round(
                measurement.length_cm
                * measurement.width_cm
                * measurement.filled_height_cm
                / measurement.pack_count,
                2,
            )
            self.evidence.volume_source = "MEASURED"
            if not 0 < self.volume_cm3 <= 1e9:
                raise ValueError("Measured volume is outside the supported range")
        if self.reviewed and (self.net_weight_kg is None or self.volume_cm3 is None):
            raise ValueError("Reviewed profiles need a positive pack weight and occupied volume")
        if self.reviewed and self.evidence.volume_source == "UNKNOWN":
            raise ValueError("Select the source of the reviewed occupied volume")
        if self.reviewed and self.evidence.weight_source == "UNKNOWN":
            raise ValueError("Select the source of the reviewed pack weight")
        if (
            self.reviewed
            and self.evidence.volume_source in {"RESEARCH", "PROXY"}
            and not self.evidence.source_url
        ):
            raise ValueError("Reviewed estimates need a research source URL")
        return self


class LimitInput(BaseModel):
    box_catalogue_id: str
    max_net_weight_kg: float = Field(gt=0, le=10000, allow_inf_nan=False)
    reviewed: bool = False
    source_note: str | None = Field(default=None, max_length=2048)


class ProductInput(BaseModel):
    expected_revision: int = Field(ge=1)
    produce_type: ProduceType
    box_material: BoxMaterial
    temperature: TemperatureZone
    control_status: ControlStatus
    weight_class: WeightClass
    botanical_family: str | None = Field(default=None, max_length=128)
    firmness: Firmness | None = None
    packing_reviewed: bool = False
    packing_notes: str | None = Field(default=None, max_length=2048)
    source_rows: list[int] = Field(default_factory=list, max_length=300)
    profiles: list[PackInput] = Field(default_factory=list, max_length=100)
    limits: list[LimitInput] = Field(default_factory=list, max_length=100)


class CreateProductInput(ProductInput):
    t_code: str = Field(min_length=1, max_length=32)
    name_en: str = Field(min_length=1, max_length=256)
    name_th: str = Field(default="", max_length=256)
    name_vi: str = Field(default="", max_length=256)


class BoxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    size_name: str = Field(min_length=1, max_length=64)
    box_type: BoxMaterial
    length_cm: float = Field(gt=0, le=9999, allow_inf_nan=False)
    width_cm: float = Field(gt=0, le=9999, allow_inf_nan=False)
    height_cm: float = Field(gt=0, le=9999, allow_inf_nan=False)
    max_safe_net_weight_kg: float = Field(gt=0, le=9999, allow_inf_nan=False)
    packing_reviewed: bool = False
    is_active: bool = True


def audit(db, actor, event, table, identity, before, after):
    db.add(
        AuditLog(
            event_type=event,
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table=table,
            target_id=identity,
            before_state=before,
            after_state=after,
        )
    )


def product_out(product):
    matches = source_matches(product)
    problems = []
    sizes = set(product.default_pack_sizes or []) | {p.pack_size for p in product.packing_profiles}
    if not sizes:
        problems.append("Add a measured pack size")
    for size in sorted(sizes):
        try:
            profile_for(product, size, strict=True)
        except ValueError as exc:
            problems.append(str(exc))
    if not product.packing_reviewed or not product.firmness:
        problems.append("Review product classifications and firmness")
    if (product.packing_source or {}).get("table"):
        from app.modules.packing.product_table import table_problems
        problems = table_problems(product)
    signatures = {
        tuple(
            row.get(key)
            for key in ["box_material", "temperature", "control_status", "weight_class"]
        )
        for row in matches
    }
    return {
        "id": product.id,
        "t_code": product.t_code,
        "name_en": product.name_en,
        "name_th": product.name_th,
        "name_vi": product.name_vi,
        **{
            key: getattr(product, key).value
            for key in [
                "produce_type",
                "box_material",
                "temperature",
                "control_status",
                "weight_class",
            ]
        },
        "botanical_family": product.botanical_family,
        "firmness": product.firmness,
        "packing_reviewed": product.packing_reviewed,
        "packing_revision": product.packing_revision,
        "packing_notes": product.packing_notes,
        "source_rows": (product.packing_source or {}).get("rows", []),
        "default_pack_sizes": product.default_pack_sizes or [],
        "ready": not problems,
        "problems": problems,
        "source_matches": matches,
        "source_conflict": len(signatures) > 1
        and not bool((product.packing_source or {}).get("rows")),
        "suggestions": suggestions(product),
        "profiles": [
            {
                "pack_size": p.pack_size,
                "aliases": p.aliases or [],
                "net_weight_kg": float(p.net_weight_kg) if p.net_weight_kg is not None else None,
                "volume_cm3": float(p.volume_cm3) if p.volume_cm3 is not None else None,
                "reviewed": p.reviewed,
                "evidence": p.evidence or {},
            }
            for p in product.packing_profiles
        ],
        "limits": [
            {
                "box_catalogue_id": limit.box_catalogue_id,
                "max_net_weight_kg": float(limit.max_net_weight_kg),
                "reviewed": limit.reviewed,
                "source_note": limit.source_note,
            }
            for limit in product.box_limits
        ],
        "research": research_for(product),
    }


@router.get("/products")
def list_setup_products(
    q: str = "",
    readiness: Literal["all", "ready", "review"] = "all",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: Literal["sku", "frequent"] = "sku",
    db: Session = Depends(get_db, scope="function"),
):
    products = (
        db.query(Product)
        .filter_by(is_active=True)
        .options(selectinload(Product.packing_profiles), selectinload(Product.box_limits))
        .order_by(Product.t_code)
        .all()
    )
    rows = [product_out(p) for p in products]
    frequency = dict(
        db.query(ClientOrderItem.product_id, func.count(ClientOrderItem.id))
        .group_by(ClientOrderItem.product_id)
        .all()
    )
    for row in rows:
        row["order_frequency"] = frequency.get(row["id"], 0)
    if sort == "frequent":
        rows.sort(key=lambda row: (-row["order_frequency"], row["t_code"]))
    summary = {
        "total_skus": len(rows),
        "ready_skus": sum(row["ready"] for row in rows),
        "matched_skus": sum(bool(row["source_matches"]) for row in rows),
        "conflicting_skus": sum(row["source_conflict"] for row in rows),
        "strict_profiles_enabled": settings.PACKING_PROFILES_ENABLED,
        "reference_rows": len(reference()["entries"]),
    }
    term = q.strip().casefold()
    rows = [
        row
        for row in rows
        if (
            not term
            or any(
                term in row[key].casefold() for key in ["t_code", "name_en", "name_th", "name_vi"]
            )
        )
        and (readiness == "all" or row["ready"] == (readiness == "ready"))
    ]
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(rows),
        "summary": summary,
    }


@router.get("/reference")
def list_reference(q: str = ""):
    term = q.strip().casefold()
    rows = [row for row in reference()["entries"] if term in row["name"].casefold()]
    return {"source": reference()["source"], "items": rows[:100], "total": len(rows)}


class StageReferenceInput(BaseModel):
    expected_revision: str = Field(min_length=64, max_length=64)


@router.get("/products/{product_id}/volume-guidance")
def volume_guidance(
    product_id: str,
    pack_size: str = Query(min_length=1, max_length=64),
    net_weight_kg: float | None = Query(default=None, ge=0.0001, le=10000, allow_inf_nan=False),
    source_rows: str = "",
    db: Session = Depends(get_db, scope="function"),
):
    product = db.query(Product).filter_by(id=product_id, is_active=True).first()
    if not product:
        raise HTTPException(404, "Active product not found")
    try:
        key = pack_key(pack_size)
        selected = [int(v) for v in source_rows.split(",") if v] if source_rows else None
    except ValueError:
        raise HTTPException(422, "Choose a valid grouping row and an explicit pack label") from None
    if selected is not None and not set(selected) <= {r["row"] for r in reference()["entries"]}:
        raise HTTPException(422, "A grouping reference row does not exist")
    guidance = research_for(product, source_rows=selected, additional_sizes=[pack_size])
    candidate = next((p for p in guidance["guidance_profiles"] if p["pack_size"] == key), None)
    if not candidate:
        raise HTTPException(422, "Choose a physical pack size")
    evidence = candidate["evidence"]
    density = next(
        (r for r in research_data()["references"] if r["id"] == evidence["reference_id"]), None
    )
    if net_weight_kg is not None and density:
        evidence.update(
            {
                "estimate_low_cm3": round(net_weight_kg * 1000 / density["high"], 2),
                "estimate_high_cm3": round(net_weight_kg * 1000 / density["low"], 2),
                "volume_source": "PROXY"
                if any(r["density_status"] != "Commodity reference" for r in guidance["rows"])
                else "RESEARCH",
            }
        )
        evidence["estimate_kind"] = evidence["volume_source"]
    return {"evidence": evidence, "density_conflict": guidance["density_conflict"]}


def reference_stage_preview(products):
    pending = pending_drafts(products)
    return {
        "revision": staging_revision(products, pending),
        "products": len({p.id for p, _ in pending}),
        "pack_profiles": len(pending),
        "with_volume_estimate": sum(
            c["evidence"]["estimate_high_cm3"] is not None for _, c in pending
        ),
    }


@router.get("/reference-drafts")
def preview_reference_drafts(db: Session = Depends(get_db, scope="function")):
    products = (
        db.query(Product)
        .filter_by(is_active=True)
        .options(selectinload(Product.packing_profiles))
        .order_by(Product.id)
        .all()
    )
    return reference_stage_preview(products)


@router.post("/reference-drafts")
def apply_reference_drafts(
    payload: StageReferenceInput,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    products = (
        db.query(Product)
        .filter_by(is_active=True)
        .options(selectinload(Product.packing_profiles))
        .order_by(Product.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    preview = reference_stage_preview(products)
    if payload.expected_revision != preview["revision"]:
        raise HTTPException(409, "Packing setup changed. Preview reference drafts again")
    pending = pending_drafts(products)
    stage_drafts(db, pending)
    audit(
        db,
        actor,
        "PACKING_REFERENCE_DRAFTS_STAGED",
        "products",
        "reference-drafts",
        preview,
        {"profiles_added": len(pending), "product_ids": sorted({p.id for p, _ in pending})},
    )
    return {"profiles_added": len(pending), "products": preview["products"]}


def save_product(db, product, payload, actor):
    if product.packing_revision != payload.expected_revision:
        raise HTTPException(409, "This product changed. Reload it before saving")
    if payload.packing_reviewed and payload.firmness is None:
        raise HTTPException(422, "Select firmness before marking classifications reviewed")
    if not set(payload.source_rows) <= {row["row"] for row in reference()["entries"]}:
        raise HTTPException(422, "An Excel reference row does not exist")
    keys, profile_keys = set(), set()
    try:
        for profile in payload.profiles:
            key = pack_key(profile.pack_size)
            aliases = {key, *(lookup_pack_key(alias) for alias in profile.aliases)}
            if keys & aliases:
                raise ValueError("Two pack profiles share a pack size or alias")
            keys.update(aliases)
            profile_keys.add(key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    limit_ids = [limit.box_catalogue_id for limit in payload.limits]
    if len(set(limit_ids)) != len(limit_ids):
        raise HTTPException(422, "Supply only one product limit for each box type")
    catalogues = {
        c.id: c
        for c in db.query(BoxCatalogue)
        .filter(BoxCatalogue.id.in_(limit_ids))
        .order_by(BoxCatalogue.id)
        .with_for_update()
    }
    for limit in payload.limits:
        catalogue = catalogues.get(limit.box_catalogue_id)
        if not catalogue or limit.max_net_weight_kg > float(catalogue.max_safe_net_weight_kg):
            raise HTTPException(
                422,
                "A product limit must refer to an existing box and cannot exceed its safe capacity",
            )
    before = product_out(product)
    for key in [
        "produce_type",
        "box_material",
        "temperature",
        "control_status",
        "weight_class",
        "botanical_family",
        "packing_reviewed",
        "packing_notes",
    ]:
        setattr(product, key, getattr(payload, key))
    product.firmness = payload.firmness.value if payload.firmness else None
    product.packing_source = {"source": reference()["source"], "rows": payload.source_rows}
    existing = {p.pack_key: p for p in product.packing_profiles}
    for key, profile in existing.items():
        if key not in profile_keys:
            db.delete(profile)
    db.flush()
    for incoming in payload.profiles:
        key = pack_key(incoming.pack_size)
        profile = existing.get(key)
        if profile is None:
            profile = ProductPackProfile(product_id=product.id, pack_key=key, revision=0)
            db.add(profile)
        for name in ["pack_size", "aliases", "net_weight_kg", "volume_cm3", "reviewed"]:
            setattr(profile, name, getattr(incoming, name))
        profile.evidence = incoming.evidence.model_dump(mode="json")
        profile.revision += 1
    for limit in list(product.box_limits):
        db.delete(limit)
    db.flush()
    for incoming in payload.limits:
        db.add(ProductBoxLimit(product_id=product.id, **incoming.model_dump()))
    product.packing_revision += 1
    db.flush()
    db.expire(product, ["packing_profiles", "box_limits"])
    after = product_out(product)
    audit(db, actor, "PRODUCT_PACKING_UPDATED", "products", product.id, before, after)
    return after


@router.put("/products/{product_id}")
def update_product(
    product_id: str,
    payload: ProductInput,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    product = (
        db.query(Product)
        .filter_by(id=product_id, is_active=True)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not product:
        raise HTTPException(404, "Active product not found")
    return save_product(db, product, payload, actor)


@router.post("/products", status_code=201)
def create_product(
    payload: CreateProductInput,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    code, name = payload.t_code.strip().upper(), payload.name_en.strip()
    if not code or not name:
        raise HTTPException(422, "Product code and name cannot be blank")
    if (
        db.query(Product)
        .filter(or_(Product.t_code == code, func.upper(Product.t_code) == code))
        .first()
    ):
        raise HTTPException(409, "This SKU already exists; edit its packing setup instead")
    product = Product(
        t_code=code,
        name_en=name,
        name_vi=payload.name_vi.strip() or name,
        name_th=payload.name_th.strip() or name,
        box_material=payload.box_material,
        temperature=payload.temperature,
        control_status=payload.control_status,
        produce_type=payload.produce_type,
        weight_class=payload.weight_class,
        default_pack_sizes=[p.pack_size for p in payload.profiles],
        packing_revision=1,
        is_active=True,
    )
    db.add(product)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "This SKU already exists; edit its packing setup instead") from exc
    return save_product(db, product, payload, actor)


def box_out(box):
    volume = external_volume_cm3(box) if reference_for(box) else None
    return {
        "id": box.id,
        "size_name": box.size_name,
        "box_type": box.box_type.value,
        **{
            key: float(getattr(box, key)) if getattr(box, key) is not None else None
            for key in [
                "length_cm",
                "width_cm",
                "height_cm",
                "max_safe_net_weight_kg",
            ]
        },
        "packing_reviewed": box.packing_reviewed,
        "packing_revision": box.packing_revision,
        "is_active": box.is_active,
        "dimension_basis": "EXTERNAL",
        "dimension_source": dimension_source(box),
        "external_volume_cm3": volume,
        "usable_volume_cm3": volume * 0.92 if volume is not None else None,
    }


@router.get("/boxes")
def list_boxes(db: Session = Depends(get_db, scope="function")):
    return [
        box_out(box)
        for box in db.query(BoxCatalogue).order_by(BoxCatalogue.box_type, BoxCatalogue.size_name)
        if reference_for(box)
    ]


def save_box(db, box, payload, actor, *, new=False):
    if not new and box.packing_revision != payload.expected_revision:
        raise HTTPException(409, "This box type changed. Reload before saving")
    try:
        require_workbook_box(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    duplicate = (
        db.query(BoxCatalogue)
        .filter(
            BoxCatalogue.box_type == payload.box_type,
            BoxCatalogue.length_cm == payload.length_cm,
            BoxCatalogue.width_cm == payload.width_cm,
            BoxCatalogue.height_cm == payload.height_cm,
            BoxCatalogue.id != box.id if not new else True,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            409, "This workbook box size already exists; edit its safe capacity instead"
        )
    if not payload.size_name.strip():
        raise HTTPException(422, "Box size name cannot be blank")
    if payload.length_cm * payload.width_cm * payload.height_cm / 6000 > 9999.99:
        raise HTTPException(422, "Shipping dimensions exceed the supported box range")
    before = box_out(box) if not new else {}
    exterior_changed = not new and any(
        float(getattr(box, key)) != getattr(payload, key)
        for key in ["length_cm", "width_cm", "height_cm"]
    )
    if (
        exterior_changed
        and db.query(Box)
        .filter(
            or_(Box.box_catalogue_id == box.id, Box.override_box_catalogue_id == box.id),
            Box.is_locked.is_(True),
            Box.shipping_dimensions_snapshot.is_(None),
        )
        .first()
    ):
        raise HTTPException(
            409,
            "Historical locked boxes use these shipping dimensions. Create a new box type instead",
        )
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(box, key, value)
    box.volume_m3 = payload.length_cm * payload.width_cm * payload.height_cm / 1_000_000
    box.rated_weight_kg = payload.length_cm * payload.width_cm * payload.height_cm / 6000
    box.packing_revision = 1 if new else box.packing_revision + 1
    if new:
        db.add(box)
    if payload.packing_reviewed:
        box_volume(box)
    db.flush()
    result = box_out(box)
    audit(db, actor, "BOX_CATALOGUE_PACKING_UPDATED", "box_catalogue", box.id, before, result)
    return result


@router.put("/boxes/{box_id}")
def update_box(
    box_id: str,
    payload: BoxInput,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    box = db.query(BoxCatalogue).filter_by(id=box_id).with_for_update().populate_existing().first()
    if not box:
        raise HTTPException(404, "Box type not found")
    return save_box(db, box, payload, actor)


@router.post("/boxes", status_code=201)
def create_box(
    payload: BoxInput,
    db: Session = Depends(get_db, scope="function"),
    actor: User = Depends(require_action(Action.FLOOR_PACKING)),
):
    box = BoxCatalogue()
    return save_box(db, box, payload, actor, new=True)
