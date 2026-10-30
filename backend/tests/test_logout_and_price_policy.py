from decimal import Decimal

import pytest
from test_pricing import _setup
from test_system_resilience_audit import audit_client as audit_client
from test_system_resilience_audit import ready_invoice

from app.api.sku_mappings import SellingPriceCreate
from app.core.security import create_access_token, decode_access_token, hash_password
from app.models.audit import AuditLog
from app.models.master_data import CustomerSellingPrice
from app.models.user import User, UserRole
from app.modules.pricing import PriceCatalogue


def test_logout_revokes_all_devices_but_preserves_another_account(audit_client, db_session):
    client, user = audit_client
    version = user.session_version
    second_device = create_access_token({"sub": user.id, "session_version": version, "device": "phone"})
    other = User(email="other@example.invalid", full_name="Other", password_hash=hash_password("Other-password-2026!"), role=UserRole.GENERAL)
    db_session.add(other)
    db_session.commit()
    other_token = create_access_token({"sub": other.id, "session_version": other.session_version})
    response = client.post("/api/v3/auth/logout")
    assert response.status_code == 204, response.text
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/v3/auth/me").status_code == 401
    assert client.get("/api/v3/auth/me", headers={"Authorization": "Bearer " + second_device}).status_code == 401
    assert client.get("/api/v3/auth/me", headers={"Authorization": "Bearer " + other_token}).status_code == 200
    db_session.refresh(user)
    assert user.session_version == version + 1


def test_delayed_old_logout_cannot_revoke_a_new_login(audit_client, db_session):
    client, user = audit_client
    old_header = client.headers["Authorization"]
    assert client.post("/api/v3/auth/logout").status_code == 204
    login = client.post("/api/v3/auth/login", json={"email": user.email, "password": "Audit-password-2026!"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    version = decode_access_token(token)["session_version"]
    assert client.post("/api/v3/auth/logout", headers={"Authorization": old_header}).status_code == 204
    assert client.get("/api/v3/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 200
    db_session.refresh(user)
    assert user.session_version == version
    events = db_session.query(AuditLog).filter_by(event_type="AUTH_LOGOUT_ALL").all()
    assert len(events) == 1
    assert old_header not in str(events[0].after_state)


def test_cookie_logout_also_revokes_legacy_bearer_tokens(audit_client):
    client, user = audit_client
    legacy = create_access_token({"sub": user.id})
    client.headers.pop("Authorization")
    client.cookies.set("access_token", legacy)
    assert client.get("/api/v3/auth/me").status_code == 200
    assert client.post("/api/v3/auth/logout").status_code == 204
    assert client.get("/api/v3/auth/me", headers={"Authorization": "Bearer " + legacy}).status_code == 401


@pytest.mark.parametrize("token", ["forged.payload.signature", "expired", "bad_claims"])
def test_untrusted_or_expired_logout_only_clears_its_cookie(audit_client, db_session, token):
    client, user = audit_client
    version = user.session_version
    if token == "expired":
        token = create_access_token({"sub": user.id, "session_version": version}, expires_delta=-1)
    elif token == "bad_claims":
        token = create_access_token({"sub": user.id, "session_version": False})
    response = client.post("/api/v3/auth/logout", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 204
    db_session.refresh(user)
    assert user.session_version == version
    assert client.get("/api/v3/auth/me").status_code == 200


def test_temporary_price_is_rejected_before_history_changes(db_session):
    actor, main, sub, product = _setup(db_session)
    prices = PriceCatalogue(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None,
                  product_id=product.id, pack_size="100g")
    prices.set_price(**common, price=50, valid_from="2026-01-01")
    audits = db_session.query(AuditLog).count()
    with pytest.raises(ValueError, match="Temporary|expiry|expire"):
        prices.set_price(**common, price=45, valid_from="2026-09-01", valid_to="2026-09-07")
    row = db_session.query(CustomerSellingPrice).one()
    assert row.valid_to is None and row.selling_price_eur == 50
    assert db_session.query(AuditLog).count() == audits
    assert prices.price_for(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, pack_size="100g", as_of="2035-01-01") == 50


def test_price_entry_rejects_a_client_expiry_date():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="Temporary|expiry|expire"):
        SellingPriceCreate(main_customer_id="main", product_id="product", pack_size="100g",
                           selling_price_eur=45, valid_from="2026-09-01", valid_to="2026-09-07")


@pytest.mark.parametrize("role,service,allowed", [
    (UserRole.OWNER_ADMIN, False, True), (UserRole.GENERAL_ACCOUNTING, False, True),
    (UserRole.GENERAL, False, False), (UserRole.AI_AGENT_1, True, False),
    (UserRole.OWNER_ADMIN, True, False),
])
def test_catalogue_changes_require_human_admin_or_finance(db_session, role, service, allowed):
    actor, main, sub, product = _setup(db_session)
    actor.role, actor.is_service_principal = role, service
    prices = PriceCatalogue(db_session)
    kwargs = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None,
                  product_id=product.id, pack_size="100g", price=45, valid_from="2026-09-01")
    if not allowed:
        with pytest.raises(PermissionError):
            prices.set_price(**kwargs)
        assert db_session.query(CustomerSellingPrice).count() == 0
        return
    row = prices.set_price(**kwargs)
    assert row.valid_to is None
    assert prices.price_for(main_customer_id=main.id, sub_customer_id=sub.id,
        product_id=product.id, pack_size="100g", as_of="2035-01-01") == Decimal("45.00")


@pytest.mark.parametrize("role,service,expected", [
    (UserRole.OWNER_ADMIN, False, 200), (UserRole.GENERAL_ACCOUNTING, False, 200),
    (UserRole.GENERAL, False, 403), (UserRole.AI_AGENT_1, True, 403),
    (UserRole.OWNER_ADMIN, True, 403),
])
def test_price_api_enforces_the_same_policy(audit_client, db_session, role, service, expected):
    client, user = audit_client
    _, main, _, product = _setup(db_session)
    user.role, user.is_service_principal = role, service
    db_session.commit()
    response = client.post("/api/v3/master-data/sku-mappings/selling-prices", json={
        "main_customer_id": main.id, "product_id": product.id, "pack_size": "100g",
        "selling_price_eur": 45, "valid_from": "2026-09-01"})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["valid_to"] is None
    else:
        assert db_session.query(CustomerSellingPrice).count() == 0


def test_catalogue_change_preserves_issued_invoice_amounts(audit_client, db_session):
    _, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
    before = (invoice.total_amount_eur, invoice.items[0].unit_price_eur)
    order = invoice.items[0].source_order
    prices = PriceCatalogue(db_session)
    prices.set_price(actor=actor, main_customer_id=order.main_customer_id,
        sub_customer_id=order.customer_id, product_id=invoice.items[0].product_id,
        pack_size=invoice.items[0].pack_size, price=45, valid_from=order.raw_file.shipment_target_date)
    db_session.commit()
    db_session.refresh(invoice)
    assert (invoice.total_amount_eur, invoice.items[0].unit_price_eur) == before
    assert prices.price_for(main_customer_id=order.main_customer_id, sub_customer_id=order.customer_id,
        product_id=invoice.items[0].product_id, pack_size=invoice.items[0].pack_size, as_of="2035-01-01") == 45


def test_backdated_price_has_only_the_next_authorized_change_as_its_boundary(db_session):
    actor, main, sub, product = _setup(db_session)
    prices = PriceCatalogue(db_session)
    common = dict(actor=actor, main_customer_id=main.id, sub_customer_id=None, product_id=product.id)
    prices.set_price(**common, pack_size="1kg", price=50, valid_from="2026-09-01")
    early = prices.set_price(**common, pack_size="1000g", price=45, valid_from="2026-01-01")
    assert early.valid_to == "2026-08-31"
    lookup = dict(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id, pack_size="1kg")
    assert prices.price_for(**lookup, as_of="2026-08-31") == 45
    assert prices.price_for(**lookup, as_of="2035-01-01") == 50
