"""Retained history, legacy contracts and bounded query fan-out regressions."""
# ruff: noqa: F811
# Pytest injects the imported shared fixture into test arguments.
import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect
from test_pricing import _setup
from test_registers import make_file, register_client  # noqa: F401

from app.core.work_limits import WorkLimit
from app.models.invoice import Invoice, InvoiceLineItem
from app.models.master_data import CustomerSellingPrice
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingOrderSource
from app.modules.packing.boxes import orders_in_group
from app.modules.pricing import PriceCatalogue

ROUTES = ["/orders/intake-queue", "/orders/{raw}/client-orders", "/procurement/source-orders",
          "/packing/approval-queue", "/invoicing/work-queue", "/invoicing"]


def seed(db, actor, route, count=101):
    _, _, customer, product = _setup(db)
    raw = make_file(db, actor)
    if route == ROUTES[0]:
        for index in range(count - 1):
            make_file(db, actor, f"history-{index:04}.xlsx")
        return raw.id, customer, product
    for index in range(count):
        source = make_file(db, actor, f"source-{index:04}.xlsx", OrderStatus.PURCHASING_CONSOLIDATED) if route == ROUTES[2] else raw
        order = ClientOrder(raw_order_file_id=source.id, customer_id=customer.id,
            order_ref_id=f"ORDER-{index:04}", status=(OrderStatus.FLOOR_PACKING if route == ROUTES[3]
                else OrderStatus.PURCHASING_CONSOLIDATED if route == ROUTES[2] else OrderStatus.HANDOFF_02_RETURNED),
            packing_approval_status="PENDING", packing_requested_at=datetime(2026, 1, 1, tzinfo=UTC))
        db.add(order)
        db.flush()
        if route == ROUTES[5]:
            invoice = Invoice(client_order_id=order.id, customer_id=customer.id, invoice_number=f"INV-{index:04}",
                final_packing_manifest_id="fixture", subtotal_eur=1, total_amount_eur=1)
            db.add(invoice)
            db.flush()
            db.add(line(invoice.id, order.id, product.id, f"line-{index:04}"))
    db.flush()
    return raw.id, customer, product


def line(invoice_id, order_id, product_id, identity):
    return InvoiceLineItem(id=identity, invoice_id=invoice_id, source_order_id=order_id, product_id=product_id,
        box_number=1, pack_size="100g", billed_pack_count=1, billed_weight_kg="0.1000", unit_price_eur=1, line_total_eur=1)


def capture(db, request):
    statements = []
    def listen(_conn, _cursor, sql, _params, _context, _many):
        statements.append(sql)
    db.expunge_all()  # A warm identity map must not hide N+1 queries.
    event.listen(db.get_bind(), "before_cursor_execute", listen)
    try:
        response = request()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listen)
    assert response.status_code == 200, response.text
    return response, statements


@pytest.mark.parametrize("route", ROUTES)
def test_legacy_large_collection_requires_paging_and_never_silently_drops_history(db_session, register_client, route):
    client, actor = register_client
    raw, _, _ = seed(db_session, actor, route)
    same_time = datetime(2026, 1, 1, tzinfo=UTC)
    for model in (RawOrderFile, ClientOrder, Invoice):
        db_session.query(model).update({model.created_at: same_time})
    db_session.flush()
    url = "/api/v3" + route.format(raw=raw)
    response = client.get(url)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PAGINATION_REQUIRED"
    assert response.json()["detail"]["total"] == 101
    first = client.get(url, params={"limit": 100})
    last = client.get(url, params={"offset": 100, "limit": 100})
    assert first.status_code == last.status_code == 200, first.text
    assert first.headers["x-total-count"] == "101" and first.headers["x-has-more"] == "true"
    assert last.headers["x-has-more"] == "false"
    def items(response):
        body = response.json()
        return [order for group in body["groups"].values() for order in group] if route == ROUTES[1] else body
    assert len(items(first)) == 100 and len(items(last)) == 1
    assert not ({str(item) for item in items(first)} & {str(item) for item in items(last)})
    assert client.get(url, params={"limit": 101}).status_code == 422
    assert client.get(url, params={"offset": -1}).status_code == 422


@pytest.mark.parametrize("route", [ROUTES[0], ROUTES[2], ROUTES[3], ROUTES[4], ROUTES[5]])
def test_ten_and_hundred_rows_have_constant_query_count(db_session, register_client, route):
    client, actor = register_client
    seed(db_session, actor, route)
    counts = []
    for size in (10, 100):
        response, statements = capture(db_session, lambda size=size: client.get("/api/v3" + route, params={"limit": size}))
        assert len(response.json()) == size
        counts.append(len(statements))
    assert counts[1] <= counts[0] + 1
    assert max(counts) <= 8, counts


def test_file_register_does_not_materialize_acknowledgment_blob(db_session, register_client):
    client, actor = register_client
    raw = make_file(db_session, actor)
    raw.acknowledgment_summary = {"drafts": ["x" * 1_000_000], "validation": {"unresolved_product_rows": 7, "can_split": True}}
    identity = raw.id
    db_session.flush()
    response, statements = capture(db_session, lambda: client.get("/api/v3/orders/files"))
    assert response.json()["items"][0]["unresolved_rows"] == 7
    assert len(response.content) < 2000
    assert "acknowledgment_summary" in inspect(db_session.get(RawOrderFile, identity)).unloaded
    assert not any("raw_order_files.acknowledgment_summary AS" in sql for sql in statements)


def test_large_legacy_review_fails_before_blob_deserialization(db_session, register_client):
    client, actor = register_client
    raw = make_file(db_session, actor)
    raw.acknowledgment_summary = {"legacy": "x" * (17 * 1024 * 1024)}
    identity = raw.id
    db_session.flush()
    db_session.expunge_all()
    assert client.get(f"/api/v3/orders/files/{identity}").status_code == 413
    assert "acknowledgment_summary" in inspect(db_session.get(RawOrderFile, identity)).unloaded


def test_invoice_line_paging_preserves_all_sources_and_large_detail_guidance(db_session, register_client, monkeypatch):
    from app.core import read_models
    client, actor = register_client
    _, _, product = seed(db_session, actor, ROUTES[5], count=1)
    invoice = db_session.query(Invoice).one()
    order = db_session.get(ClientOrder, invoice.client_order_id)
    db_session.add_all([line(invoice.id, order.id, product.id, f"extra-{i}") for i in range(2)])
    db_session.flush()
    monkeypatch.setattr(read_models, "MAX_DETAIL_LINES", 2)
    monkeypatch.setattr(read_models, "MAX_PAGE_LINES", 2)
    assert client.get(f"/api/v3/invoicing/{invoice.id}").status_code == 409
    assert client.get(f"/api/v3/invoicing/{invoice.id}/html").status_code == 409
    assert client.get("/api/v3/invoicing?limit=1").status_code == 409
    first = client.get(f"/api/v3/invoicing/{invoice.id}/lines?limit=2").json()
    second = client.get(f"/api/v3/invoicing/{invoice.id}/lines?limit=2&offset=2").json()
    assert first["total"] == 3 and first["has_more"] and not second["has_more"]
    assert len({item["line_item_id"] for item in first["items"] + second["items"]}) == 3
    assert all(item["source_order_id"] == order.id for item in first["items"] + second["items"])
    assert client.get("/api/v3/invoicing/missing/lines").status_code == 404


def test_master_memberships_include_direct_anchor_and_secondary_sources(db_session, register_client):
    client, actor = register_client
    raw, customer, product = seed(db_session, actor, ROUTES[4], count=3)
    orders = db_session.query(ClientOrder).order_by(ClientOrder.order_ref_id).all()
    invoice = Invoice(client_order_id=orders[0].id, customer_id=customer.id, invoice_type="MASTER_SUMMARY",
        invoice_number="MASTER-1", final_packing_manifest_id="fixture", subtotal_eur=1, total_amount_eur=1)
    db_session.add(invoice)
    db_session.flush()
    db_session.add(line(invoice.id, orders[1].id, product.id, "source-secondary"))
    db_session.flush()
    response = client.get("/api/v3/invoicing/work-queue").json()
    by_id = {item["order_id"]: item["invoice_id"] for item in response}
    assert by_id == {orders[0].id: invoice.id, orders[1].id: invoice.id, orders[2].id: None}
    assert client.get("/api/v3/invoicing/orders").json()["total"] == 1


def test_invoice_summaries_defer_notes_and_detail_budget_counts_utf8_bytes(db_session, register_client, monkeypatch):
    from app.core import read_models
    client, actor = register_client
    seed(db_session, actor, ROUTES[5], count=1)
    invoice = db_session.query(Invoice).one()
    invoice.internal_notes = "ก" * 10
    identity = invoice.id
    db_session.flush()
    db_session.expunge_all()
    assert client.get("/api/v3/invoicing/records").status_code == 200
    assert "internal_notes" in inspect(db_session.get(Invoice, identity)).unloaded
    monkeypatch.setattr(read_models, "MAX_DETAIL_NOTE_BYTES", 20)
    response = client.get(f"/api/v3/invoicing/{identity}")
    assert response.status_code == 409
    assert "internal_notes" in inspect(db_session.get(Invoice, identity)).unloaded


def test_procurement_explicit_order_source_is_searchable_and_visible(db_session, register_client):
    client, actor = register_client
    _, customer, _ = seed(db_session, actor, ROUTES[4], count=1)
    order = db_session.query(ClientOrder).one()
    raw = order.raw_file
    raw.main_customer_id = customer.id
    batch = MasterPurchasingList(batch_reference="EXPLICIT", total_skus_count=0)
    db_session.add(batch)
    db_session.flush()
    db_session.add(PurchasingOrderSource(purchasing_list_id=batch.id, client_order_id=order.id))
    db_session.flush()
    response = client.get("/api/v3/procurement/batches", params={"customer": customer.client_code, "q": raw.file_name}).json()
    assert response["total"] == 1
    assert response["items"][0]["files"] == [raw.file_name]
    assert response["items"][0]["customers"] == [customer.company_name]
    detail = client.get(f"/api/v3/procurement/batches/{batch.id}")
    assert detail.status_code == 200
    assert detail.json()["source_files"][0]["file_id"] == raw.id


def test_oversized_source_page_fails_instead_of_omitting_members(db_session, register_client, monkeypatch):
    import app.api.registers as registers
    from app.core.read_models import bounded_headers
    monkeypatch.setattr(registers, "bounded_headers", lambda query: bounded_headers(query, maximum=1))
    client, actor = register_client
    raw = make_file(db_session, actor)
    other = make_file(db_session, actor, "other.xlsx")
    batch = MasterPurchasingList(batch_reference="WIDE", raw_order_file_id=raw.id, total_skus_count=0)
    db_session.add(batch)
    db_session.flush()
    db_session.add(PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=other.id))
    db_session.flush()
    response = client.get("/api/v3/procurement/batches")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DETAIL_PAGE_TOO_WIDE"


def test_group_budget_is_checked_before_returning_any_orders(db_session, register_client):
    _, actor = register_client
    _, customer, _ = seed(db_session, actor, ROUTES[3], count=501)
    with pytest.raises(WorkLimit, match="500 orders"):
        orders_in_group(db_session, "ALL", None)
    assert db_session.query(ClientOrder).filter(ClientOrder.status == OrderStatus.FLOOR_PACKING).count() == 501


def test_six_thousand_ended_prices_do_not_block_new_price_or_rewrite_old_values(db_session):
    actor, main, sub, product = _setup(db_session)
    begin = date(2000, 1, 1)
    db_session.execute(CustomerSellingPrice.__table__.insert(), [{"id": f"price-{i:05}",
        "main_customer_id": main.id, "sub_customer_id": None, "product_id": product.id, "pack_size": "100g",
        "selling_price_eur": 1, "valid_from": (begin + timedelta(days=i)).isoformat(),
        "valid_to": (begin + timedelta(days=i)).isoformat()} for i in range(6000)])
    catalogue = PriceCatalogue(db_session)
    catalogue.set_price(actor=actor, main_customer_id=main.id, sub_customer_id=None,
        product_id=product.id, pack_size="100g", price=2, valid_from="2026-01-01")
    assert catalogue.price_for(main_customer_id=main.id, sub_customer_id=sub.id, product_id=product.id,
        pack_size="100 grams", as_of="2035-01-01") == 2
    assert db_session.get(CustomerSellingPrice, "price-00000").valid_to == "2000-01-01"
    assert db_session.query(CustomerSellingPrice).count() == 6001


def test_price_index_migration_repeat_and_downgrade_preserve_rows():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261027_0022_price_read_indexes.py"
    spec = importlib.util.spec_from_file_location("price_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for table, _, scope in module.INDEXES:
            fields = ",".join(f"{field} TEXT" for field in scope)
            conn.exec_driver_sql(f"CREATE TABLE {table} (id TEXT PRIMARY KEY,{fields},valid_from TEXT)")
            conn.exec_driver_sql(f"INSERT INTO {table} (id,valid_from) VALUES ('kept','20260101')")
        module.upgrade()
        module.upgrade()
        for _table, name, _ in module.INDEXES:
            assert conn.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='index' AND name=?", (name,)).scalar() == 1
        module.downgrade()
        module.downgrade()
        for table, _, _ in module.INDEXES:
            assert tuple(conn.exec_driver_sql(f"SELECT id,valid_from FROM {table}").one()) == ("kept", "20260101")
    engine.dispose()
