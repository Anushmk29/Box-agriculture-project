from decimal import Decimal

from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    Customer,
    CustomerSellingPrice,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.user import User, UserRole
from app.modules.pricing import PriceCatalogue


def _setup(db):
    actor = User(email="acc@example.com", password_hash="x", full_name="Acc", role=UserRole.GENERAL_ACCOUNTING)
    main = Customer(client_code="TONIO", company_name="T", billing_email="t@x.eu", route_group="TETE",
                    destination_country="France", is_main_customer=True)
    product = Product(t_code="T1", name_vi="Sả", name_en="Lemongrass", name_th="ตะไคร้",
                      box_material=BoxMaterial.CARDBOARD, temperature=TemperatureZone.NORMAL,
                      control_status=ControlStatus.COMBINABLE, produce_type=ProduceType.HERB,
                      weight_class=WeightClass.LIGHT)
    db.add_all([actor, main, product])
    db.flush()
    sub = Customer(client_code="CT-A", company_name="A", billing_email="a@x.eu", route_group="GROUP CT",
                   destination_country="France", parent_customer_id=main.id)
    db.add(sub)
    db.flush()
    return actor, main, sub, product


def test_new_effective_date_keeps_history_and_same_date_replaces(db_session):
    actor, main, sub, product = _setup(db_session)
    catalogue = PriceCatalogue(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None, product_id=product.id, pack_size="100g")

    catalogue.set_price(**common, price=1.80, valid_from="2026-01-01")
    catalogue.set_price(**common, price=2.00, valid_from="2026-09-01")
    catalogue.set_price(**common, price=2.10, valid_from="2026-09-01")

    rows = db_session.query(CustomerSellingPrice).order_by(CustomerSellingPrice.valid_from).all()
    assert [(r.valid_from, r.valid_to, float(r.selling_price_eur)) for r in rows] == [
        ("2026-01-01", "2026-08-31", 1.80),
        ("2026-09-01", None, 2.10),
    ]
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="100g")
    assert catalogue.price_for(**lookup, as_of="2026-08-15") == Decimal("1.80")
    assert catalogue.price_for(**lookup, as_of="2026-09-07") == Decimal("2.10")


def test_sub_customer_price_beats_general_and_missing_price_is_none(db_session):
    actor, main, sub, product = _setup(db_session)
    catalogue = PriceCatalogue(db_session)
    base = dict(actor=actor, main_customer_id=main.id, product_id=product.id, pack_size="100g", valid_from="2026-01-01")
    catalogue.set_price(**base, sub_customer_id=None, price=1.80)
    catalogue.set_price(**base, sub_customer_id=sub.id, price=1.60)
    catalogue.set_price(actor=actor, main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="500g", price=4.20, valid_from="2026-01-01")

    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, as_of="2026-09-07")
    assert catalogue.price_for(**lookup, pack_size="100g") == Decimal("1.60")
    assert catalogue.price_for(**lookup, pack_size="100g tray") == Decimal("1.60")
    assert catalogue.price_for(**lookup, pack_size="KG (500g)") == Decimal("4.20")
    assert catalogue.price_for(**lookup, pack_size="250g") is None


def test_gram_aliases_use_same_size_price_without_falling_back_to_other_sizes(db_session):
    actor, main, sub, product = _setup(db_session)
    catalogue = PriceCatalogue(db_session)
    catalogue.set_price(actor=actor, main_customer_id=main.id, sub_customer_id=None,
        product_id=product.id, pack_size="200g", price=3.80, valid_from="2026-01-01")
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, as_of="2026-10-06")

    for label in ("200gr.", "200Gr.", "200 gr", "200 grams"):
        assert catalogue.price_for(**lookup, pack_size=label) == Decimal("3.80")
    assert catalogue.price_for(**lookup, pack_size="80Gr.") is None
