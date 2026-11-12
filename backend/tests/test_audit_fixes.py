"""Regressions for the seven-point repository audit."""

import importlib.util
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from test_api_security import client_for
from test_invoicing_invariant import build_order_with_box
from test_pricing import _setup

from app.core import login_limits
from app.main import app
from app.models import CustomerSellingPrice, InvoiceStatus, LoginLimit, PriceList, User, UserRole
from app.modules.orders.intake import OrderIntake
from app.modules.orders.quantities import derive_quantity
from app.modules.orders.templates import require_template_review, template_snapshots
from app.modules.pricing import PriceCatalogue, PriceCatalogueReviewRequired
from app.services.excel_profiles import parse_profiles, references
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService
from app.services.pdf_order_parser import PdfOrderParser


@pytest.mark.parametrize("heading", ["Order-117", "New shop", None])
@pytest.mark.parametrize("summary", ["Total", "Gross Weight"])
@pytest.mark.parametrize("quantity", [5, "=2+3"])
def test_matrix_customer_after_summary_requires_review(tmp_path, heading, summary, quantity):
    definition = deepcopy(next(p for p in references() if p["key"] == "HUNGFAT-MATRIX"))
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["HUNGFAT"])
    sheet.append(["code no", "Name Eng", None, None, "Packing", "Order-101", "Order -103",
                  "Order-110", "Order-116", summary, heading])
    sheet.append(["11001", "Lemongrass", None, None, 80, 1, 0, 0, 0, 6, quantity])
    path = tmp_path / "Hungfat.xlsx"
    workbook.save(path)
    drafts = parse_profiles(path, [definition])
    assert drafts[0].items[0].quantity == 1
    with pytest.raises(ValueError, match="Unmapped customer/order column.*K"):
        require_template_review(drafts)


def test_generic_pdf_preserves_units_and_cannot_enter_purchasing_without_review():
    parser = object.__new__(PdfOrderParser)
    draft = parser._parse_generic_pdf_table("Apples 5 box\nBananas 12 pcs\nHerbs 2,5 kgs\nFruit -3 pack", "new.pdf")
    assert [(item.quantity, item.unit) for item in draft.items] == [(5, "box"), (12, "pc"), (2.5, "kg"), (0, "pack")]
    for item in draft.items:
        assert item.quantity_issues
        assert item.source["raw_unit"] in {"box", "pcs", "kgs", "pack"}
        with pytest.raises(ValueError, match="Confirm the customer PDF format"):
            derive_quantity(item)
    assert draft.items[-1].source["raw_quantity"] == "-3"


def test_intake_only_uses_supplied_customer_details(db_session):
    intake = OrderIntake(db_session)
    missing = intake.get_or_create_customer("NEW-SHOP", route_group="GENERAL", parent_id=None)
    assert (missing.company_name, missing.billing_email, missing.destination_country) == ("NEW-SHOP", "", "")
    supplied = intake.get_or_create_customer("PINTO", route_group="GENERAL", parent_id=None,
                                             company_name="Pinto Shop", country="Maldives")
    assert (supplied.company_name, supplied.destination_country, supplied.billing_email) == ("Pinto Shop", "Maldives", "")
    supplied.billing_email = "orders@example.com"
    assert intake.get_or_create_customer("PINTO", route_group="GENERAL", parent_id=None).billing_email == "orders@example.com"


def test_dispatch_requires_missing_customer_details_to_be_completed(db_session):
    from app.api.invoicing import render_on_behalf_invoice_html

    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).first()
    actor.role = UserRole.GENERAL_ACCOUNTING
    actor.is_service_principal = False
    service = OnBehalfInvoicingService(db_session)
    invoice = service.generate_sub_customer_invoice(order.id, actor=actor)
    order.customer.destination_country = ""
    order.customer.billing_email = ""
    rendered = render_on_behalf_invoice_html(invoice.id, db=db_session).body.decode()
    assert "clienttrade.eu" not in rendered and "Europe" not in rendered
    assert "Not provided" in rendered
    with pytest.raises(ValueError, match="Complete destination country and billing email"):
        service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    assert invoice.status == InvoiceStatus.INVOICE_DRAFT
    order.customer.destination_country = "Thailand"
    order.customer.billing_email = "orders@example.com"
    assert service.approve_and_dispatch(invoice_id=invoice.id, actor=actor).status == InvoiceStatus.INVOICE_SENT


def test_seeded_template_reads_never_write_or_lock_customer(db_session):
    snapshots = template_snapshots(db_session, "GENERAL")
    db_session.commit()
    statements = []
    def capture(_connection, clause, *_args):
        statements.append(clause)
    event.listen(db_session.get_bind(), "before_execute", capture)
    try:
        repeated = template_snapshots(db_session, "GENERAL")
    finally:
        event.remove(db_session.get_bind(), "before_execute", capture)
    assert {row["id"] for row in repeated} == {row["id"] for row in snapshots}
    assert len(statements) == 1
    assert statements[0]._for_update_arg is None


@pytest.mark.parametrize("definition", [p for p in references() if p["key"].startswith("NGA-REGIONAL")], ids=lambda p: p["key"])
def test_reviewed_nga_reference_catalogue_is_not_an_order_column(definition):
    path = Path(__file__).resolve().parents[2] / "documents" / definition.get("sample_directory", "new input format") / definition["sample"]
    if not path.exists():
        pytest.skip("Local source workbook unavailable")
    from app.api.order_templates import validate_definition

    validate_definition(definition)
    drafts = parse_profiles(path, [definition])
    require_template_review(drafts)


def test_login_counters_survive_rollback_and_are_shared_between_sessions(db_session, monkeypatch):
    monkeypatch.setattr(login_limits.time, "time", lambda: 1800)
    for _ in range(login_limits.ACCOUNT_ATTEMPTS):
        login_limits.enforce_login_limit(db_session, " SHOP@EXAMPLE.COM ", "192.0.2.1")
        db_session.rollback()
    with Session(db_session.get_bind()) as other:
        with pytest.raises(HTTPException) as error:
            login_limits.enforce_login_limit(other, "shop@example.com", "192.0.2.2")
        assert error.value.status_code == 429
        assert error.value.headers["Retry-After"] == "900"
    assert all(len(row.key) == 64 for row in db_session.query(LoginLimit))
    monkeypatch.setattr(login_limits.time, "time", lambda: 2700)
    login_limits.enforce_login_limit(db_session, "shop@example.com", "192.0.2.1")
    assert all(row.window_start == 2700 for row in db_session.query(LoginLimit).populate_existing())


def test_http_login_blocks_before_password_verification_and_ignores_spoofed_forwarding(db_session, monkeypatch):
    from app.api import auth

    monkeypatch.setattr(login_limits, "ACCOUNT_ATTEMPTS", 2)
    monkeypatch.setattr(login_limits, "IP_ATTEMPTS", 3)
    user = User(email="limited@example.com", password_hash="unused", full_name="Limited", role=UserRole.GENERAL)
    db_session.add(user)
    db_session.commit()
    verified = []
    monkeypatch.setattr(auth, "verify_password", lambda *args: verified.append(args) or False)
    client = client_for(db_session)
    try:
        for index in range(2):
            assert client.post("/api/v3/auth/login", json={"email": user.email, "password": "wrong"},
                               headers={"X-Forwarded-For": f"192.0.2.{index}"}).status_code == 401
        blocked = client.post("/api/v3/auth/login", json={"email": user.email, "password": "wrong"})
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
        assert len(verified) == 2 and "access_token" not in blocked.cookies
        assert client.post("/api/v3/auth/login", json={"email": "other@example.com", "password": "wrong"},
                           headers={"X-Forwarded-For": "203.0.113.99"}).status_code == 429
    finally:
        app.dependency_overrides.clear()


def test_bulk_missing_prices_use_two_reads_instead_of_eight_per_line(db_session):
    requests = [dict(main_customer_id="main", sub_customer_id="shop", product_id=f"p{i}",
                     pack_size="100g", as_of="2026-11-08") for i in range(20)]
    statements = []
    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        catalogue = PriceCatalogue(db_session)
        catalogue.preload(requests)
        assert [catalogue.price_for(**request) for request in requests] == [None] * 20
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert len(statements) == 2


@pytest.mark.parametrize("pack", ["100g", "100 grams", "100g tray", "KG (100g)", "500g", "200g"])
@pytest.mark.parametrize("day", ["2025-01-01", "2026-06-01", "2026-11-08"])
def test_bulk_prices_preserve_scope_pack_and_historical_rules(db_session, pack, day):
    _, main, sub, product = _setup(db_session)
    db_session.add_all([
        PriceList(product_id=product.id, customer_id=None, pack_size="100g", unit_price_eur=4, valid_from="2020-01-01"),
        PriceList(product_id=product.id, customer_id=sub.id, pack_size="500g", unit_price_eur=8, valid_from="2020-01-01"),
        CustomerSellingPrice(main_customer_id=main.id, product_id=product.id, pack_size="100g", selling_price_eur=3, valid_from="20260101"),
        CustomerSellingPrice(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="100g", selling_price_eur=2, valid_from="2026-01-01", valid_to="2026-08-31"),
        CustomerSellingPrice(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="100g", selling_price_eur=Decimal("2.50"), valid_from="2026-09-01"),
    ])
    db_session.flush()
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size=pack, as_of=day)
    expected = PriceCatalogue(db_session).price_for(**lookup)
    catalogue = PriceCatalogue(db_session)
    catalogue.preload([lookup])
    assert catalogue.price_for(**lookup) == expected


def test_bulk_prices_detect_alias_conflicts_and_invalidate_after_writes(db_session):
    actor, main, sub, product = _setup(db_session)
    catalogue = PriceCatalogue(db_session)
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="100g", as_of="2026-11-08")
    catalogue.preload([lookup])
    assert catalogue.price_for(**lookup) is None
    catalogue.set_price(actor=actor, main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
                        pack_size="100g", price=2, valid_from="2026-01-01")
    assert catalogue.price_for(**lookup) == 2
    db_session.add(CustomerSellingPrice(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
                                       pack_size="100 grams", selling_price_eur=3, valid_from="2026-01-01"))
    db_session.flush()
    catalogue.preload([lookup])
    with pytest.raises(PriceCatalogueReviewRequired, match="Conflicting prices"):
        catalogue.price_for(**lookup)


def test_bulk_ignores_bad_lower_priority_history_but_blocks_it_when_needed(db_session):
    _, main, sub, product = _setup(db_session)
    db_session.add_all([
        CustomerSellingPrice(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
                             pack_size="100g", selling_price_eur=2, valid_from="2026-01-01"),
        PriceList(product_id=product.id, pack_size="100g", unit_price_eur=3, valid_from="2026-02-30"),
    ])
    db_session.flush()
    requests = [dict(main_customer_id=main.id, sub_customer_id=customer, product_id=product.id,
                     pack_size="100g", as_of="2026-11-08") for customer in (sub.id, "other")]
    catalogue = PriceCatalogue(db_session)
    catalogue.preload(requests)
    assert catalogue.price_for(**requests[0]) == 2
    with pytest.raises(PriceCatalogueReviewRequired, match="Invalid stored price date"):
        catalogue.price_for(**requests[1])


@pytest.mark.parametrize("many_labels", [False, True])
def test_bulk_preserves_price_history_limits(db_session, many_labels):
    _, main, sub, product = _setup(db_session)
    db_session.add_all([PriceList(product_id=product.id, pack_size=f"{index}g" if many_labels else "100g",
                                  unit_price_eur=2, valid_from="2026-01-01") for index in range(501)])
    db_session.flush()
    request = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
                   pack_size="100g", as_of="2026-11-08")
    catalogue = PriceCatalogue(db_session)
    catalogue.preload([request])
    with pytest.raises(PriceCatalogueReviewRequired, match="Too many"):
        catalogue.price_for(**request)


def test_bulk_chunks_equivalent_labels_without_losing_price_conflict_checks(db_session):
    _, main, _, product = _setup(db_session)
    db_session.add_all([PriceList(product_id=product.id, pack_size=label, unit_price_eur=2,
                                  valid_from="2026-01-01")
                        for label in ("100g", "100 g", "100gr", "100 gr", "100Gr.", "100 grams")])
    db_session.flush()
    requests = [dict(main_customer_id=main.id, sub_customer_id=f"shop-{index}", product_id=product.id,
                     pack_size="100g", as_of="2026-11-08") for index in range(200)]
    catalogue = PriceCatalogue(db_session)
    catalogue.preload(requests)
    assert [catalogue.price_for(**request) for request in requests] == [2] * 200


@pytest.mark.parametrize("fresh", [False, True])
def test_login_limit_migration_repeats_and_preserves_counters(fresh):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261108_0026_login_limits.py"
    spec = importlib.util.spec_from_file_location("login_limit_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        if fresh:
            LoginLimit.__table__.create(connection)
        module.upgrade()
        connection.execute(LoginLimit.__table__.insert().values(key="a" * 64, window_start=1800, attempts=3))
        module.upgrade()
        assert connection.exec_driver_sql("SELECT attempts FROM login_limits").scalar_one() == 3
        assert "ix_login_limits_window_start" in {index["name"] for index in inspect(connection).get_indexes("login_limits")}
        module.downgrade()
        assert "login_limits" not in inspect(connection).get_table_names()
    engine.dispose()
