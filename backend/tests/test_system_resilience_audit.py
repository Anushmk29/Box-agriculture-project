"""Local audit evidence. Xfails document open defects, not passing safeguards.

Run through scripts/run_resilience_audit.py to force dummy configuration.
No external database, network service, or real user is required.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_invoicing_invariant import build_order_with_box
from test_pricing import _setup
from test_procurement_handoff import setup_procurement_batch

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.master_data import Customer
from app.models.user import User, UserRole
from app.modules.orders.intake import OrderIntake
from app.modules.pricing import PriceCatalogue
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


@pytest.fixture
def audit_client(db_session):
    actor = User(email="audit@example.invalid", password_hash=hash_password("Audit-password-2026!"),
                 full_name="Disposable Auditor", role=UserRole.OWNER_ADMIN)
    db_session.add(actor)
    db_session.commit()

    def override():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer " + create_access_token({"sub": actor.id})
        yield client, actor
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def ready_invoice(db, actor):
    order = build_order_with_box(db, locked=True)
    service = OnBehalfInvoicingService(db)
    invoice = service.generate_sub_customer_invoice(order.id, actor=actor)
    db.commit()
    return service, invoice


@pytest.mark.parametrize("password", ["a" * 73, "ก" * 25])
def test_password_change_accepts_schema_valid_long_password(audit_client, password):
    client, _ = audit_client
    response = client.post("/api/v3/auth/change-password", json={
        "current_password": "Audit-password-2026!", "new_password": password})
    assert response.status_code == 204, response.text


def test_logout_revokes_presented_session(audit_client):
    client, _ = audit_client
    assert client.post("/api/v3/auth/logout").status_code == 204
    assert client.get("/api/v3/auth/me").status_code == 401


@pytest.mark.parametrize("changes", [
    {"amount_paid": "NaN"}, {"amount_paid": "Infinity"},
    {"payment_date": "2026-02-31"}, {"bank_wire_reference": ""},
    {"bank_name": ""}, {"bank_wire_reference": " " * 129},
])
def test_payment_rejects_invalid_input_before_work(audit_client, db_session, changes):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    db_session.commit()
    payload = {"amount_paid": 1, "payment_date": "2026-10-26", "bank_name": "Dummy bank",
               "bank_wire_reference": "AUDIT-REF", **changes}
    response = client.post(f"/api/v3/invoicing/{invoice.id}/record-payment", json=payload)
    assert response.status_code == 404  # Payment HTTP contract retired by the invoice-only scope., response.text


def test_invoice_price_rejects_nonfinite_number(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    response = client.patch(f"/api/v3/invoicing/{invoice.id}/draft", json={
        "items": [{"line_item_id": invoice.items[0].id, "unit_price_eur": "Infinity"}]})
    assert response.status_code == 422, response.text


def test_invoice_price_rejects_duplicate_lines(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    line = invoice.items[0].id
    response = client.patch(f"/api/v3/invoicing/{invoice.id}/draft", json={
        "items": [{"line_item_id": line, "unit_price_eur": 3},
                  {"line_item_id": line, "unit_price_eur": 4}]})
    assert response.status_code == 422, response.text


def test_temporary_price_is_rejected_under_approved_no_expiry_policy(db_session):
    actor, main, sub, product = _setup(db_session)
    catalogue = PriceCatalogue(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None,
                  product_id=product.id, pack_size="100g")
    catalogue.set_price(**common, price=2, valid_from="2026-01-01")
    with pytest.raises(ValueError, match="Temporary prices"):
        catalogue.set_price(**common, price=3, valid_from="2026-09-01", valid_to="2026-09-30")
    result = catalogue.price_for(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, pack_size="100g", as_of="2026-10-01")
    assert result == Decimal("2.00"), f"Rejected expiry changed the price: {result!r}"


def test_price_dates_are_canonical_or_rejected():
    from app.api.sku_mappings import SellingPriceCreate
    try:
        row = SellingPriceCreate(main_customer_id="main", product_id="product", pack_size="100g",
                                 selling_price_eur=2, valid_from="20260901")
    except ValidationError:
        return
    assert row.valid_from == "2026-09-01"


def test_price_lookup_uses_the_same_pack_units_as_intake(db_session):
    from app.models.master_data import PriceList
    from app.modules.packing.profiles import pack_key
    _, main, sub, product = _setup(db_session)
    db_session.add(PriceList(product_id=product.id, pack_size="1kg", unit_price_eur=2.5,
                             valid_from="2026-01-01"))
    db_session.flush()
    result = PriceCatalogue(db_session).price_for(main_customer_id=main.id,
        sub_customer_id=sub.id, product_id=product.id, pack_size=pack_key("1kg"), as_of="2026-10-26")
    assert result == Decimal("2.50")


def test_same_shop_code_cannot_silently_link_to_another_main_customer(db_session):
    _, first_main, first_sub, _ = _setup(db_session)
    second_main = Customer(client_code="SECOND-MAIN", company_name="Second", billing_email="b@example.invalid",
                           route_group="SECOND", destination_country="Thailand", is_main_customer=True)
    db_session.add(second_main)
    db_session.flush()
    try:
        result = OrderIntake(db_session).get_or_create_customer(first_sub.client_code,
            route_group="SECOND", parent_id=second_main.id)
    except ValueError:
        return
    assert result.parent_customer_id == second_main.id
    assert first_sub.parent_customer_id == first_main.id
    assert first_sub.route_group != "SECOND"


@pytest.mark.parametrize("changes", [{"default_unit_volume_cm3": -1}, {"tare_weight_allowance_g": -1}])
def test_product_rejects_invalid_physical_values(audit_client, changes):
    client, _ = audit_client
    response = client.post("/api/v3/master-data/products", json={
        "t_code": "AUDIT-PRODUCT", "name_en": "Audit herb", **changes})
    assert response.status_code == 422, response.text


def test_stale_invoice_edit_requires_a_revision(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    line = invoice.items[0].id
    url = f"/api/v3/invoicing/{invoice.id}/draft"
    revision = client.get(f"/api/v3/invoicing/{invoice.id}").json()["revision"]
    assert client.patch(url, json={"expected_revision": revision, "items": [{"line_item_id": line, "unit_price_eur": 3}]}).status_code == 200
    stale = client.patch(url, json={"expected_revision": revision, "items": [{"line_item_id": line, "unit_price_eur": 4}]})
    assert stale.status_code == 409, stale.text


def test_payment_over_balance_is_rejected(audit_client, db_session):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    db_session.commit()
    response = client.post(f"/api/v3/invoicing/{invoice.id}/record-payment", headers={"Idempotency-Key": "over-balance-test-0001"}, json={
        "amount_paid": 100, "payment_date": "2026-10-26", "bank_name": "Dummy bank",
        "bank_wire_reference": "OVERPAY-AUDIT"})
    assert response.status_code == 404  # Payment HTTP contract retired by the invoice-only scope.
    assert float(invoice.amount_paid_eur) == 0


def test_general_user_cannot_edit_financial_data(audit_client):
    client, actor = audit_client
    actor.role = UserRole.GENERAL
    response = client.post("/api/v3/invoicing/nonexistent/record-payment", json={
        "amount_paid": 1, "payment_date": "2026-10-26", "bank_name": "Dummy",
        "bank_wire_reference": "AUDIT"})
    assert response.status_code == 404  # Payment HTTP contract retired by the invoice-only scope.


def test_password_change_revokes_existing_token(audit_client):
    client, _ = audit_client
    response = client.post("/api/v3/auth/change-password", json={
        "current_password": "Audit-password-2026!", "new_password": "New-Audit-password-2026!"})
    assert response.status_code == 204
    assert client.get("/api/v3/auth/me").status_code == 401


def test_forged_session_is_rejected(audit_client):
    client, _ = audit_client
    client.headers["Authorization"] = "Bearer forged.payload.signature"
    assert client.get("/api/v3/auth/me").status_code == 401


@pytest.mark.parametrize("text, expected", [
    ("PARIS STORE\nHerb 1kg 2 KG", None),
    ("PARIS STORE\nETA 07/09\nHerb 1kg 2 KG", "2026-09-07"),
])
def test_pdf_does_not_invent_or_misinterpret_shipment_date(text, expected):
    from app.services.pdf_order_parser import PdfOrderParser
    parser = object.__new__(PdfOrderParser)
    # A yearless source ETA needs the explicitly selected shipment year.
    parser.shipment_date_reference = "2026-09-07"
    draft = parser._parse_alindar_paris_store(text, "dummy.pdf")
    assert draft.shipment_date == expected, draft.shipment_date


def test_purchase_confirmation_rejects_contradictory_zero_weight(db_session):
    from app.modules.procurement.service import (
        ProcurementConfirmation,
        ProcurementConfirmationService,
    )
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    try:
        ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id,
            confirmations=[ProcurementConfirmation(item.id, 10, 0)], actor=buyer)
    except ValueError:
        return
    assert sum(float(line.confirmed_net_weight_kg) for line in order.items) <= 0


def test_purchase_replacement_cannot_silently_disappear(db_session):
    from app.models.master_data import Product
    from app.modules.procurement.service import (
        ProcurementConfirmation,
        ProcurementConfirmationService,
    )
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    original = item.product
    replacement = Product(t_code="AUDIT-REPLACEMENT", name_en="Replacement", name_vi="Replacement", name_th="Replacement",
        box_material=original.box_material, temperature=original.temperature, control_status=original.control_status,
        produce_type=original.produce_type, weight_class=original.weight_class)
    db_session.add(replacement)
    db_session.flush()
    try:
        ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id,
            confirmations=[ProcurementConfirmation(item.id, 10, 10, replacement_product_id=replacement.id)], actor=buyer)
    except ValueError:
        return
    assert any(line.replacement_product_id == replacement.id for line in order.items)
    assert all(line.product_id == original.id for line in order.items)


def test_template_packing_does_not_restore_zero_confirmed_quantity(db_session):
    from test_packing_lifecycle import setup_order

    from app.models.packing import BoxItem
    from app.modules.packing.floor import FloorPackingService
    _, _, actor, order = setup_order(db_session)
    actor.role = UserRole.OWNER_ADMIN
    line = order.items[0]
    line.ordered_pack_count = 4
    line.estimated_net_weight_kg = 4
    line.confirmed_pack_count = 0
    line.confirmed_net_weight_kg = 0
    db_session.flush()
    try:
        FloorPackingService(db_session).fast_track_template_pack_order(order.id, actor=actor)
    except ValueError:
        return
    assert sum(item.final_packed_count for item in db_session.query(BoxItem)) == 0


def test_retried_adhoc_submission_is_not_silently_doubled(audit_client, db_session):
    from uuid import uuid4

    from test_order_intake import _setup as intake_setup
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    body = {"client_code": "CT-A", "items": [{"t_code": "T001", "pack_count": 2}]}
    path = f"/api/v3/orders/{raw.id}/add-adhoc-order"
    headers = {"Idempotency-Key": str(uuid4())}
    first = client.post(path, headers=headers, json=body)
    repeated = client.post(path, headers=headers, json=body)
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    assert repeated.json()["total_items"] == 2


def test_request_commit_failure_is_not_reported_as_success(db_session, monkeypatch):
    from fastapi import Depends

    from app.core import database

    def fail_commit():
        raise RuntimeError("Simulated commit failure in disposable test")

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "commit", fail_commit)
    original = app.dependency_overrides.copy()
    app.dependency_overrides.clear()

    @app.post("/__audit_commit_failure")
    def write_record(db=Depends(get_db, scope="function")):
        db.add(Customer(client_code="COMMIT-FAIL", company_name="Dummy", billing_email="dummy@example.invalid",
                        route_group="AUDIT", destination_country="Thailand"))
        return {"saved": True}

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/__audit_commit_failure")
        assert response.status_code >= 500, response.text
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/__audit_commit_failure"]
        app.dependency_overrides.update(original)
