from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from test_invoicing_invariant import build_order_with_box
from test_order_intake import _draft, _item, _setup
from test_pricing import _setup as pricing_setup

from app.api.orders import upload_order_file_universal, upload_order_matrix
from app.api.sku_mappings import SellingPriceCreate
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.master_data import Customer, CustomerSellingPrice, PriceList
from app.models.order import ClientOrder, RawOrderFile
from app.models.user import User, UserRole
from app.modules.orders.intake import OrderIntake
from app.modules.pricing import PriceCatalogue
from app.services.adaptive_order_orchestrator import AdaptiveOrderOrchestrator
from app.services.pdf_order_parser import PdfOrderParser
from app.services.unified_excel_parser import UnifiedExcelParser


@pytest.mark.parametrize("value", ["20260901", "2026-09-01", " 2026-09-01 "])
def test_price_dates_are_stored_as_one_calendar_format(value):
    payload = SellingPriceCreate(main_customer_id="main", product_id="product", pack_size="100g",
                                 selling_price_eur=2, valid_from=value)
    assert payload.valid_from == "2026-09-01"


@pytest.mark.parametrize("upload,suffix", [(upload_order_matrix, ".xlsx"), (upload_order_file_universal, ".pdf")])
@pytest.mark.parametrize("day", ["2026-02-30", "not a date", "2026-13-01"])
def test_invalid_shipment_date_rejected_before_file_read(db_session, upload, suffix, day):
    raw, _ = _setup(db_session)
    stream = BytesIO(b"invalid file content must not be read")
    with pytest.raises(HTTPException) as error:
        upload(file=UploadFile(filename="bad" + suffix, file=stream), shipment_date=day,
               route_line="TETE", main_customer_id=raw.main_customer_id, db=db_session, actor=None)
    assert error.value.status_code == 422
    assert stream.tell() == 0


def test_document_date_conflict_does_not_override_selected_shipment(db_session, tmp_path, monkeypatch):
    path = tmp_path / "date-conflict.xlsx"
    path.write_bytes(b"parser replaced by fixture")
    draft = _draft(_item(2))
    draft.shipment_date = "2026-09-10"
    from app.services import adaptive_order_orchestrator
    monkeypatch.setattr(adaptive_order_orchestrator, "parse_document",
        lambda *args, **kwargs: {"drafts": [draft.model_dump(mode="json")]})
    actor = User(email="dates@example.invalid", password_hash="unused", full_name="Dates", role=UserRole.GENERAL)
    db_session.add(actor)
    db_session.flush()
    with pytest.raises(ValueError, match="date.*conflict|conflict.*date"):
        AdaptiveOrderOrchestrator(db_session).ingest_file(str(path), "2026-10-26", actor=actor)
    assert db_session.query(RawOrderFile).count() == db_session.query(Customer).count() == 0


@pytest.mark.parametrize("selected", ["missing", "subcustomer"])
def test_universal_intake_rejects_invalid_selected_main_before_parse(db_session, tmp_path, monkeypatch, selected):
    raw, _ = _setup(db_session)
    main_id = "missing"
    if selected == "subcustomer":
        sub = OrderIntake(db_session).get_or_create_customer("SHOP", route_group="TETE", parent_id=raw.main_customer_id)
        main_id = sub.id
    path = tmp_path / "selected.xlsx"
    path.write_bytes(b"parser must not run")

    def parse(self):
        pytest.fail("Invalid selected main customer reached the parser")

    monkeypatch.setattr(UnifiedExcelParser, "parse", parse)
    with pytest.raises(ValueError, match="main customer"):
        AdaptiveOrderOrchestrator(db_session).ingest_file(str(path), "2026-10-26", actor=None,
                                                         main_customer_id=main_id)
    assert db_session.query(RawOrderFile).count() == 1


def test_customer_conflict_preflight_leaves_earlier_drafts_untouched(db_session):
    raw, _ = _setup(db_session)
    intake = OrderIntake(db_session)
    other_main = Customer(client_code="OTHER", company_name="Other", billing_email="other@example.invalid",
        route_group="OTHER", destination_country="France", is_main_customer=True)
    db_session.add(other_main)
    db_session.flush()
    existing = intake.get_or_create_customer("EXISTING", route_group="OTHER", parent_id=other_main.id)
    before = db_session.query(Customer).count()
    with pytest.raises(ValueError, match="parent|main customer"):
        intake.materialize(raw_file=raw, drafts=[_draft(_item(1), client="NEW"), _draft(_item(1), client="EXISTING")])
    assert db_session.query(Customer).count() == before
    assert db_session.query(ClientOrder).count() == 0
    assert existing.route_group == "OTHER"
    assert existing.parent_customer_id == other_main.id


@pytest.mark.parametrize("requested", ["1kg", "1000g", "1 kg", "1000 grams"])
def test_equivalent_pack_weights_use_existing_price(db_session, requested):
    _, main, sub, product = pricing_setup(db_session)
    db_session.add(PriceList(product_id=product.id, pack_size="1kg", unit_price_eur=2.5,
                             valid_from="2026-01-01"))
    db_session.flush()
    assert PriceCatalogue(db_session).price_for(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, pack_size=requested, as_of="2026-10-26") == 2.5


def test_packaging_prices_remain_distinct_and_alias_conflicts_require_review(db_session):
    actor, main, sub, product = pricing_setup(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None,
                  product_id=product.id, valid_from="2026-01-01")
    prices = PriceCatalogue(db_session)
    prices.set_price(**common, pack_size="1kg bag", price=2)
    prices.set_price(**common, pack_size="1000g tray", price=3)
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
                  as_of="2026-10-26")
    assert prices.price_for(**lookup, pack_size="1000g bag") == 2
    assert prices.price_for(**lookup, pack_size="1kg tray") == 3
    db_session.add_all([CustomerSellingPrice(main_customer_id=main.id, product_id=product.id,
        pack_size=size, selling_price_eur=price, valid_from="2026-01-01")
        for size, price in [("1kg", 2), ("1000g", 5)]])
    db_session.flush()
    with pytest.raises(ValueError, match="[Cc]onflicting.*price"):
        prices.price_for(**lookup, pack_size="1kg")


def test_pdf_missing_year_is_not_invented_and_explicit_reference_is_used():
    parser = object.__new__(PdfOrderParser)
    text = "PARIS STORE\nETA 07/09\nHerb 1kg 2 KG"
    assert parser._parse_alindar_paris_store(text, "order.pdf").shipment_date is None
    parser.shipment_date_reference = "2027-09-07"
    draft = parser._parse_alindar_paris_store(text, "order.pdf")
    assert draft.shipment_date == "2027-09-07"
    assert parser._parse_alindar_paris_store("PARIS STORE\nHerb 1kg 2 KG", "order.pdf").shipment_date is None


def test_price_check_explains_equivalent_pack_collision_without_writing_invoice(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).first()
    actor.role = UserRole.GENERAL_ACCOUNTING
    actor.is_service_principal = actor.must_change_password = False
    product_id = db_session.query(PriceList).one().product_id
    db_session.add(PriceList(product_id=product_id, pack_size="1000g", unit_price_eur=5,
                             valid_from="2026-01-01"))
    db_session.flush()
    original_overrides = app.dependency_overrides.copy()

    def session():
        yield db_session

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/v3/invoicing/price-check", json={"order_ids": [order.id]})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "PRICE_CATALOGUE_REVIEW_REQUIRED"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


def test_equivalent_new_price_preserves_old_effective_period(db_session):
    actor, main, sub, product = pricing_setup(db_session)
    prices = PriceCatalogue(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None, product_id=product.id)
    prices.set_price(**common, pack_size="1kg", price=2, valid_from="20260101")
    prices.set_price(**common, pack_size="1000g", price=3, valid_from="2026-09-01")
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="1 kg")
    assert prices.price_for(**lookup, as_of="20260831") == 2
    assert prices.price_for(**lookup, as_of="2026-09-01") == 3
    assert {row.pack_size for row in db_session.query(CustomerSellingPrice)} == {"1000g"}


def test_legacy_compact_price_dates_resolve_without_rewriting_history(db_session):
    _, main, sub, product = pricing_setup(db_session)
    legacy = PriceList(product_id=product.id, pack_size="1kg", unit_price_eur=2.5,
                       valid_from="20260101", valid_to="20261231")
    db_session.add(legacy)
    db_session.flush()
    assert PriceCatalogue(db_session).price_for(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, pack_size="1000g", as_of="2026-10-26") == 2.5
    assert legacy.valid_from == "20260101"


def test_materialization_checks_cached_draft_dates_before_customer_creation(db_session):
    raw, _ = _setup(db_session)
    draft = _draft(_item(1))
    draft.shipment_date = "2027-01-01"
    before = db_session.query(Customer).count()
    with pytest.raises(ValueError, match="date conflicts"):
        OrderIntake(db_session).materialize(raw_file=raw, drafts=[draft])
    assert db_session.query(Customer).count() == before
    assert db_session.query(ClientOrder).count() == 0
