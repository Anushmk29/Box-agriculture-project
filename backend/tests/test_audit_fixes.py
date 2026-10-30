import pytest
from fastapi.testclient import TestClient
from test_invoicing_invariant import build_order_with_box
from test_simplified_workflow import headers, prepared

from app.api.invoicing import render_on_behalf_invoice_html
from app.api.operations import operations_summary
from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models.audit import AgentHandoff
from app.models.invoice import Invoice, InvoiceStatus
from app.models.master_data import BoxCatalogue, BoxMaterial, Product, TemperatureZone
from app.models.packing import Box, BoxItem
from app.models.user import User, UserRole
from app.modules.handoffs.service import canonical_sha256
from app.modules.packing.boxes import new_box
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.service import FinalPackingService
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


def test_self_transfer_keeps_contents_and_readings(db_session):
    actor, _, box = prepared(db_session)
    box.items[0].final_net_weight_kg = 2
    box.actual_net_weight_kg = 2
    db_session.flush()
    with pytest.raises(ValueError, match="must be different"):
        FloorPackingService(db_session).transfer_box_contents(box.id, box.id, actor=actor)
    assert db_session.query(BoxItem).count() == 1
    assert box.actual_net_weight_kg == 2


@pytest.mark.parametrize("operation", ["transfer", "move", "finalize"])
def test_temperature_segregation_applies_to_floor_and_finalization(db_session, operation):
    actor, order, source = prepared(db_session)
    p = source.items[0].product
    warm = Product(t_code="WARM", name_vi="Warm", name_en="Warm", name_th="Warm",
        box_material=p.box_material, temperature=TemperatureZone.NORMAL, control_status=p.control_status,
        produce_type=p.produce_type, weight_class=p.weight_class, default_unit_volume_cm3=100)
    db_session.add(warm)
    db_session.flush()
    target = new_box(order, source.box_catalogue_id, 2, route_code=source.route_code)
    db_session.add(target)
    db_session.flush()
    db_session.add(BoxItem(box_id=target.id, product_id=warm.id, pack_size="1kg",
        final_packed_count=1, final_net_weight_kg=1))
    db_session.flush()
    service = FloorPackingService(db_session)
    with pytest.raises(ValueError, match="incompatible"):
        if operation == "transfer":
            service.transfer_box_contents(source.id, target.id, actor=actor)
        elif operation == "move":
            service.move_items_between_boxes(source.id, target.id, p.id, "1kg", 1, actor=actor)
        else:
            source.items[0].final_net_weight_kg = 3
            db_session.add(BoxItem(box_id=source.id, product_id=warm.id, pack_size="1kg",
                final_packed_count=1, final_net_weight_kg=1))
            db_session.flush()
            FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    assert not source.is_locked


@pytest.mark.parametrize("dims", [(-40, 30, 20), (0, 30, 20), (40, None, 20),
    (float("nan"), 30, 20), (float("inf"), 30, 20), (1, 1, 1)])
def test_invalid_or_too_small_custom_box_is_rejected(db_session, dims):
    actor, _, box = prepared(db_session)
    with pytest.raises(ValueError):
        FloorPackingService(db_session).override_box_size(box.id, actor=actor,
            custom_length_cm=dims[0], custom_width_cm=dims[1], custom_height_cm=dims[2])
    assert box.custom_length_cm is None


def test_resize_cannot_change_required_material(db_session):
    actor, _, box = prepared(db_session)
    catalogue = BoxCatalogue(box_type=BoxMaterial.CARDBOARD, size_name="Wrong material",
        length_cm=38, width_cm=32, height_cm=18, rated_weight_kg=5,
        max_safe_net_weight_kg=5, volume_m3=.024)
    db_session.add(catalogue)
    db_session.flush()
    with pytest.raises(ValueError, match="material"):
        FloorPackingService(db_session).override_box_size(box.id, actor=actor, new_catalogue_id=catalogue.id)
    assert box.override_box_catalogue_id is None


@pytest.mark.parametrize("operation", ["move", "transfer"])
def test_floor_moves_cannot_overfill_usable_volume(db_session, operation):
    actor, order, source = prepared(db_session)
    catalogue = BoxCatalogue(box_type=BoxMaterial.FOAM, size_name="Small volume",
        length_cm=30, width_cm=20, height_cm=24, rated_weight_kg=5,
        max_safe_net_weight_kg=5, volume_m3=.0144)
    db_session.add(catalogue)
    db_session.flush()
    target = new_box(order, catalogue.id, 2, route_code=source.route_code)
    db_session.add(target)
    db_session.flush()
    product = source.items[0].product
    product.default_unit_volume_cm3 = 8000
    db_session.add(BoxItem(box_id=target.id, product_id=product.id, pack_size="1kg",
        final_packed_count=1, final_net_weight_kg=1))
    db_session.flush()
    with pytest.raises(ValueError, match="usable box volume"):
        if operation == "move":
            FloorPackingService(db_session).move_items_between_boxes(source.id, target.id,
                product.id, "1kg", 1, actor=actor)
        else:
            FloorPackingService(db_session).transfer_box_contents(source.id, target.id, actor=actor)
    assert source.items[0].final_packed_count == 4


def test_finalization_derives_totals_from_adjusted_items(db_session):
    actor, order, box = prepared(db_session)
    # Stale readings from an older version cannot override the current contents.
    box.actual_net_weight_kg = 99
    box.actual_gross_weight_kg = 100
    FloorPackingService(db_session).save_item(box.id, actor=actor, product_id=box.items[0].product_id,
        pack_size="1kg", quantity=4, net_weight_kg=3.8, item_id=box.items[0].id)
    FinalPackingService(db_session).finalize_order(order_id=order.id, actor=actor)
    assert box.is_locked
    assert float(box.actual_net_weight_kg) == 3.8
    assert float(box.actual_gross_weight_kg) == 4.3


def test_new_invoice_is_thb_but_historical_currency_is_preserved(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor)
    assert invoice.currency == "THB"
    assert "Amount (THB)" in render_on_behalf_invoice_html(invoice.id, db_session).body.decode()
    invoice.currency = "EUR"
    db_session.flush()
    assert "Amount (EUR)" in render_on_behalf_invoice_html(invoice.id, db_session).body.decode()
    assert OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor).currency == "EUR"


def test_signed_historical_manifest_must_also_reconcile_item_weights(db_session):
    import copy
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    box = db_session.query(Box).one()
    box.actual_net_weight_kg = 1.8
    handoff = db_session.query(AgentHandoff).one()
    payload = copy.deepcopy(handoff.payload_json)
    payload["boxes"][0]["actual_net_weight_kg"] = 1.8
    handoff.payload_json = payload
    handoff.payload_sha256 = canonical_sha256(payload)
    db_session.flush()
    with pytest.raises(ValueError, match="Reconcile item weights"):
        OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor)
    assert db_session.query(Invoice).count() == 0


def test_dashboard_does_not_report_out_of_scope_payment_balances(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=actor)
    invoice.status = InvoiceStatus.INVOICE_SENT
    accounting = User(email="currency-accounting@example.com", password_hash="unused",
        full_name="Accounting", role=UserRole.GENERAL_ACCOUNTING)
    db_session.add(accounting)
    db_session.add(Invoice(invoice_number="HISTORICAL-EUR", client_order_id=order.id,
        customer_id=order.customer_id, final_packing_manifest_id="historical", subtotal_eur=7,
        total_amount_eur=7, amount_paid_eur=2, currency="EUR", status=InvoiceStatus.INVOICE_SENT))
    db_session.flush()
    summary = operations_summary(db_session, accounting)
    assert summary["billing_scope"] == "INVOICE_ONLY"
    assert "open_balances_by_currency" not in summary
    assert "open_balance_eur" not in summary


@pytest.mark.parametrize("operation", ["change", "reset"])
def test_password_changes_and_resets_revoke_previous_sessions(db_session, operation):
    actor, _, _ = prepared(db_session)
    actor.password_hash = hash_password("original-password-123")
    owner = User(email="owner-reset@example.com", full_name="Owner", password_hash="unused", role=UserRole.OWNER_ADMIN)
    db_session.add(owner)
    db_session.commit()
    old = headers(actor)
    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
    app.dependency_overrides[get_db] = transaction
    try:
        with TestClient(app) as client:
            if operation == "change":
                response = client.post("/api/v3/auth/change-password", headers=old,
                    json={"current_password": "original-password-123", "new_password": "replacement-password-123"})
                password = "replacement-password-123"
                assert response.status_code == 204
            else:
                response = client.post(f"/api/v3/admin/users/{actor.id}/reset-password", headers=headers(owner))
                assert response.status_code == 200
                password = response.json()["temporary_password"]
            assert client.get("/api/v3/auth/me", headers=old).status_code == 401
            fresh = client.post("/api/v3/auth/login", json={"email": actor.email, "password": password})
            assert fresh.status_code == 200
            assert client.get("/api/v3/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"}).status_code == 200
    finally:
        app.dependency_overrides.clear()
