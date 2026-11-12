import pytest
from fastapi.testclient import TestClient
from test_invoicing_invariant import build_order_with_box

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models import CustomerSellingPrice, Invoice, OrderStatus, PriceList, User, UserRole
from app.models.packing import Box
from app.modules.pricing import PriceCatalogue


@pytest.fixture
def pricing_client(db_session):
    order = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).first()
    actor.role = UserRole.GENERAL_ACCOUNTING
    actor.is_service_principal = False
    actor.must_change_password = False
    db_session.query(PriceList).delete()
    db_session.commit()
    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
    app.dependency_overrides[get_db] = transaction
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        yield TestClient(app), order, actor
    finally:
        app.dependency_overrides.clear()


def test_resolution_saves_shipment_price_and_invoice_uses_it(db_session, pricing_client):
    client, order, _ = pricing_client
    blocked = client.post('/api/v3/invoicing/generate-on-behalf', json={'client_order_id': order.id})
    assert blocked.status_code == 409
    assert blocked.json()['detail']['code'] == 'MISSING_SELLING_PRICES'
    issue = client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'][0]
    assert issue['shipment_date'] == '2026-09-10'
    item = issue['items'][0]
    response = client.post('/api/v3/invoicing/resolve-prices', json={'items': [{
        'order_id': order.id, 'product_id': item['product_id'], 'pack_size': '1kg', 'selling_price': 12.75,
    }]})
    assert response.status_code == 200
    price = db_session.query(CustomerSellingPrice).one()
    assert price.sub_customer_id == order.customer_id
    assert price.valid_from == '2026-09-10' and price.valid_to is None
    assert order.status == OrderStatus.HANDOFF_02_RETURNED
    assert client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'] == []
    invoice = client.post('/api/v3/invoicing/generate-on-behalf', json={'client_order_id': order.id})
    assert invoice.status_code == 200
    assert invoice.json()['items'][0]['unit_price_eur'] == 12.75
    assert invoice.json()['total_amount_eur'] == 25.50
    lookup = dict(main_customer_id=order.main_customer_id, sub_customer_id=order.customer_id,
                  product_id=item['product_id'], pack_size='1kg')
    assert PriceCatalogue(db_session).price_for(**lookup, as_of='2035-09-11') == 12.75


def test_resolution_rejects_wrong_pack_and_does_not_overwrite_existing_price(db_session, pricing_client):
    client, order, _ = pricing_client
    issue = client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'][0]
    payload = {'order_id': order.id, 'product_id': issue['items'][0]['product_id'], 'pack_size': '150g', 'selling_price': 10}
    assert client.post('/api/v3/invoicing/resolve-prices', json={'items': [payload]}).status_code == 422
    assert db_session.query(CustomerSellingPrice).count() == 0
    payload['pack_size'] = '1kg'
    assert client.post('/api/v3/invoicing/resolve-prices', json={'items': [payload]}).status_code == 200
    payload['selling_price'] = 20
    assert client.post('/api/v3/invoicing/resolve-prices', json={'items': [payload]}).status_code == 409
    assert float(db_session.query(CustomerSellingPrice).one().selling_price_eur) == 10
    assert db_session.query(Invoice).count() == 0


def test_resolution_requires_accounting_and_completed_packing(db_session, pricing_client):
    client, order, actor = pricing_client
    order.status = OrderStatus.FLOOR_PACKING
    db_session.flush()
    assert client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).status_code == 409
    actor.role = UserRole.GENERAL
    assert client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).status_code == 403


def test_bulk_price_check_has_bounded_reads_for_many_orders(db_session, pricing_client):
    from sqlalchemy import event

    client, first, _ = pricing_client
    ids = [first.id]
    for index in range(19):
        order = build_order_with_box(db_session, locked=True, suffix=f"batch-{index}", seller=first.main_customer)
        ids.append(order.id)
    db_session.query(PriceList).delete()
    db_session.commit()
    db_session.expunge_all()
    statements = []
    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        response = client.post('/api/v3/invoicing/price-check', json={'order_ids': ids})
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert {issue['order_id'] for issue in response.json()['issues']} == set(ids)
    assert len(statements) <= 8


def test_bulk_price_check_rejects_oversized_packing_before_loading_it(pricing_client, monkeypatch):
    from app.services import on_behalf_invoicing_service

    client, order, _ = pricing_client
    monkeypatch.setattr(on_behalf_invoicing_service, "MAX_ITEMS", 0)
    response = client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]})
    assert response.status_code == 413
    assert "Select fewer orders" in response.text


def test_resolution_supports_orders_without_main_customer(db_session, pricing_client):
    client, order, _ = pricing_client
    order.main_customer_id = None
    db_session.flush()
    issue = client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'][0]
    assert client.post('/api/v3/invoicing/resolve-prices', json={'items': [{
        'order_id': order.id, 'product_id': issue['items'][0]['product_id'], 'pack_size': '1kg', 'selling_price': 8,
    }]}).status_code == 200
    row = db_session.query(PriceList).one()
    assert row.customer_id == order.customer_id
    assert row.valid_to is None
    assert client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'] == []


def test_failed_resolution_rolls_back_all_prices(db_session, pricing_client):
    client, order, _ = pricing_client
    issue = client.post('/api/v3/invoicing/price-check', json={'order_ids': [order.id]}).json()['issues'][0]
    line = {'order_id': order.id, 'product_id': issue['items'][0]['product_id'], 'pack_size': '1kg', 'selling_price': 10}
    response = client.post('/api/v3/invoicing/resolve-prices', json={'items': [line, {**line, 'pack_size': '500g'}]})
    assert response.status_code == 422
    assert db_session.query(CustomerSellingPrice).count() == 0


@pytest.mark.parametrize("conflicting", [False, True])
def test_multiple_dates_for_one_customer_use_one_permanent_price(db_session, pricing_client, conflicting):
    client, first, _ = pricing_client
    second = build_order_with_box(db_session, locked=True, suffix="SECOND", seller=first.main_customer)
    second.customer = first.customer
    original_product = db_session.query(Box).filter_by(client_order_id=first.id).one().items[0].product
    db_session.query(Box).filter_by(client_order_id=second.id).one().items[0].product = original_product
    second.raw_file.shipment_target_date = "2026-09-11"
    # The fixture helper seeds legacy prices; both selected prices must be missing.
    db_session.query(PriceList).delete()
    db_session.commit()
    issues = client.post('/api/v3/invoicing/price-check', json={'order_ids': [first.id, second.id]}).json()['issues']
    lines = [{'order_id': issue['order_id'], 'product_id': issue['items'][0]['product_id'],
              'pack_size': '1kg', 'selling_price': 10 if not conflicting or issue['order_id'] == first.id else 12}
             for issue in issues]
    assert len(lines) == 2
    response = client.post('/api/v3/invoicing/resolve-prices', json={'items': list(reversed(lines))})
    assert response.status_code == (422 if conflicting else 200), response.text
    if conflicting:
        assert db_session.query(CustomerSellingPrice).count() == 0
    else:
        row = db_session.query(CustomerSellingPrice).one()
        assert row.valid_from == '2026-09-10' and row.valid_to is None
        for day in ['2026-09-10', '2026-09-11', '2035-01-01']:
            assert PriceCatalogue(db_session).price_for(main_customer_id=first.main_customer_id,
                sub_customer_id=first.customer_id, product_id=row.product_id, pack_size='1kg', as_of=day) == 10
