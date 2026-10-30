from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.audit import AuditLog
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.master_data import Customer
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingOrderSource
from app.models.user import User, UserRole


@pytest.fixture
def register_client(db_session):
    actor = User(email="register@example.com", password_hash="unused", full_name="Owner", role=UserRole.OWNER_ADMIN)
    db_session.add(actor)
    db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: actor
    try:
        yield TestClient(app), actor
    finally:
        app.dependency_overrides.clear()


def make_file(db, actor, name="order.xlsx", status=OrderStatus.PARSED, customer=None, day="2026-07-01"):
    raw = RawOrderFile(file_name=name, storage_url="unused", shipment_target_date=day,
                       route_line="BKK", uploaded_by=actor.id, status=status,
                       main_customer_id=customer.id if customer else None,
                       acknowledgment_summary={"validation": {"unresolved_product_rows": 2}})
    db.add(raw)
    db.flush()
    return raw


def test_file_register_searches_beyond_first_page_and_keeps_old_pending(db_session, register_client):
    client, actor = register_client
    for index in range(60):
        make_file(db_session, actor, f"file-{index:03}.xlsx")
    closed = make_file(db_session, actor, "closed.xlsx", OrderStatus.ORDER_CLOSED)
    result = client.get("/api/v3/orders/files").json()
    assert result["total"] == 60 and len(result["items"]) == 25 and result["has_more"]
    result = client.get("/api/v3/orders/files?q=file-000").json()
    assert result["total"] == 1 and result["items"][0]["unresolved_rows"] == 2
    assert client.get("/api/v3/orders/files?view=completed").json()["items"][0]["raw_file_id"] == closed.id
    first = client.get("/api/v3/orders/files?sort=name&direction=asc").json()
    second = client.get("/api/v3/orders/files?sort=name&direction=asc&offset=25").json()
    assert not ({row["raw_file_id"] for row in first["items"]} & {row["raw_file_id"] for row in second["items"]})
    assert client.get("/api/v3/orders/files?date_from=2026-10-01").json()["total"] == 0
    assert client.get("/api/v3/orders/files?date_from=2026-07-01&date_to=2026-07-01").json()["total"] == 60
    assert client.get(f"/api/v3/orders/files/{closed.id}").status_code == 200
    assert client.get("/api/v3/orders/files?sort=sql_injection").status_code == 422
    assert client.get("/api/v3/orders/files?limit=101").status_code == 422
    assert client.get("/api/v3/orders/files?date_from=2026-10-01&date_to=2026-07-01").status_code == 422
    assert client.get("/api/v3/orders/files?date_from=2026-02-30").status_code == 422
    assert client.get("/api/v3/orders/files?status=UNKNOWN").status_code == 422


def test_batch_names_combined_sources_and_filters(db_session, register_client):
    client, actor = register_client
    customers = [Customer(client_code=name, company_name=name, billing_email="unused", destination_country="TH", route_group="BKK") for name in ["Zulu", "Alpha", "Beta"]]
    db_session.add_all(customers)
    db_session.flush()
    batch = MasterPurchasingList(batch_reference="PUR-COMBINED", total_skus_count=0, shipment_date="2026-07-01", route_code="BKK")
    db_session.add(batch)
    db_session.flush()
    for customer in customers:
        raw = make_file(db_session, actor, customer.company_name + ".xlsx", customer=customer)
        db_session.add(PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=raw.id))
    db_session.flush()
    result = client.get("/api/v3/procurement/batches?view=pending&customer=Zulu&route=BKK").json()
    assert result["total"] == 1
    assert result["items"][0]["display_name"] == "Alpha, Beta +1 clients · 01 Jul 2026 · BKK"
    assert result["items"][0]["customers"] == ["Alpha", "Beta", "Zulu"]
    assert client.get("/api/v3/procurement/batches?q=PUR-COMBINED&customer=Missing").json()["total"] == 0
    detail = client.get(f"/api/v3/procurement/batches/{batch.id}").json()
    assert len(detail["source_files"]) == 3


def test_workflow_scope_includes_direct_combined_and_explicit_orders(db_session, register_client):
    client, actor = register_client
    customer = Customer(client_code="SCOPE", company_name="Scope", billing_email="unused", destination_country="TH", route_group="BKK")
    db_session.add(customer)
    db_session.flush()
    files = [make_file(db_session, actor, f"scope-{i}.xlsx") for i in range(4)]
    orders = [ClientOrder(raw_order_file_id=raw.id, customer_id=customer.id,
                         order_ref_id=f"SCOPE-{i}", status=OrderStatus.HANDOFF_01_SENT)
              for i, raw in enumerate(files)]
    # A sibling of an explicitly selected order must not leak into the batch.
    sibling = ClientOrder(raw_order_file_id=files[2].id, customer_id=customer.id,
                          order_ref_id="UNSELECTED", status=OrderStatus.HANDOFF_01_SENT)
    batch = MasterPurchasingList(raw_order_file_id=files[0].id, batch_reference="SCOPED", total_skus_count=0)
    db_session.add_all([*orders, sibling, batch])
    db_session.flush()
    db_session.add_all([
        PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=files[0].id),
        PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=files[1].id),
        PurchasingOrderSource(purchasing_list_id=batch.id, client_order_id=orders[2].id),
    ])
    db_session.flush()
    result = client.get(f"/api/v3/packing/orders?batch_id={batch.id}").json()
    assert result["total"] == 3
    assert {row["order_id"] for row in result["items"]} == {order.id for order in orders[:3]}
    for raw in files[:3]:
        result = client.get(f"/api/v3/procurement/batches?raw_file_id={raw.id}").json()
        assert result["total"] == 1 and result["items"][0]["batch_id"] == batch.id
    assert client.get(f"/api/v3/procurement/batches?raw_file_id={files[3].id}").json()["total"] == 0
    assert client.get("/api/v3/packing/orders?batch_id=missing").status_code == 404
    assert client.get("/api/v3/procurement/batches?raw_file_id=missing").status_code == 404


def test_shipment_sort_places_undated_batches_last(db_session, register_client):
    client, _ = register_client
    for index, day in enumerate([None, "2026-10-17", "2026-10-15", ""]):
        db_session.add(MasterPurchasingList(batch_reference=f"SORT-{index}", shipment_date=day, total_skus_count=0))
    db_session.flush()
    result = client.get("/api/v3/procurement/batches?sort=shipment_date&direction=asc").json()
    assert [row["batch_reference"] for row in result["items"]][:2] == ["SORT-2", "SORT-1"]


def test_ten_thousand_batches_are_bounded_and_stable(db_session, register_client):
    client, _ = register_client
    db_session.execute(MasterPurchasingList.__table__.insert(), [{"id": f"batch-{index:05}",
        "batch_reference": f"PUR-{index:05}", "total_skus_count": 0,
        "status": OrderStatus.PURCHASING_CONSOLIDATED,
        "created_at": datetime(2026, 7 + index % 3, 1, tzinfo=UTC)} for index in range(10_000)])
    queries = []
    def capture(_conn, _cursor, statement, _params, _context, _many):
        queries.append(statement)
    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        response = client.get("/api/v3/procurement/batches?view=pending&offset=9975&limit=25")
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 10_000 and len(page["items"]) == 25 and not page["has_more"]
    assert len(queries) <= 4
    assert all("items" not in row for row in page["items"])
    assert client.get("/api/v3/procurement/batches?q=PUR-00000").json()["total"] == 1


def test_packing_billing_summary_counts_and_lazy_invoice_lines(db_session, register_client):
    client, actor = register_client
    customer = Customer(client_code="LY", company_name="Client LY", billing_email="unused", destination_country="TH", route_group="BKK")
    db_session.add(customer)
    db_session.flush()
    raw = make_file(db_session, actor, customer=customer)
    packing = ClientOrder(raw_order_file_id=raw.id, customer_id=customer.id, order_ref_id="PACK", status=OrderStatus.FLOOR_PACKING)
    ready = ClientOrder(raw_order_file_id=raw.id, customer_id=customer.id, order_ref_id="READY", status=OrderStatus.HANDOFF_02_RETURNED)
    billed = ClientOrder(raw_order_file_id=raw.id, customer_id=customer.id, order_ref_id="BILLED", status=OrderStatus.INVOICES_DRAFTED)
    db_session.add_all([packing, ready, billed])
    db_session.flush()
    invoice = Invoice(client_order_id=billed.id, customer_id=customer.id, invoice_number="INV-01", final_packing_manifest_id="manifest", subtotal_eur=10, total_amount_eur=10, status=InvoiceStatus.INVOICE_DRAFT)
    db_session.add(invoice)
    db_session.flush()
    packing_page = client.get("/api/v3/packing/orders?customer=LY").json()
    assert packing_page["total"] == 1
    assert packing_page["items"][0]["boxes"] == 0
    assert "boxes" not in client.get("/api/v3/invoicing/orders").json()["items"][0]
    assert client.get("/api/v3/invoicing/orders").json()["items"][0]["order_id"] == ready.id
    assert client.get("/api/v3/invoicing/orders?view=completed").json()["total"] == 1
    records = client.get("/api/v3/invoicing/records").json()
    assert records["total"] == 1 and "items" not in records["items"][0]
    assert client.get(f"/api/v3/invoicing/order-record/{billed.id}").json()["invoice_id"] == invoice.id
    assert client.get(f"/api/v3/orders/files/{raw.id}/orders").json()["total"] == 3
    assert client.get("/api/v3/invoicing/records?invoice_group=drafts&view=all").json()["total"] == 1
    assert client.get("/api/v3/invoicing/records?invoice_group=unpaid&view=all").json()["total"] == 0
    summary = client.get("/api/v3/operations/summary").json()
    assert summary["awaiting_billing"] == 2  # One ready order and one draft invoice.
    assert summary["billing_scope"] == "INVOICE_ONLY"
    assert "open_balances_by_currency" not in summary
    invoice.status = InvoiceStatus.PARTIALLY_PAID
    invoice.amount_paid_eur = 3
    db_session.flush()
    assert client.get("/api/v3/invoicing/records?invoice_group=unpaid&view=all").json()["total"] == 1
    assert client.get("/api/v3/operations/summary").json()["awaiting_billing"] == 1
    invoice.status = InvoiceStatus.CONFIRMED_PAID
    invoice.amount_paid_eur = 10
    db_session.flush()
    assert client.get("/api/v3/invoicing/records?invoice_group=paid&view=all").json()["total"] == 1
    assert client.get("/api/v3/operations/summary").json()["awaiting_billing"] == 1


def test_secondary_registers_and_permission_gates(db_session, register_client):
    client, actor = register_client
    for index in range(35):
        db_session.add(Customer(client_code=f"C-{index:02}", company_name=f"Customer {index:02}", is_main_customer=True, billing_email="unused", destination_country="TH", route_group="BKK"))
    db_session.flush()
    assert client.get("/api/v3/master-data/customer-register?offset=25").json()["total"] == 35
    assert client.get("/api/v3/master-data/customer-register?q=Customer 00").json()["total"] == 1
    assert client.get("/api/v3/admin/users/register").json()["total"] == 1
    actor.role = UserRole.GENERAL
    for endpoint in ["/invoicing/orders", "/invoicing/records", "/admin/users/register"]:
        assert client.get("/api/v3" + endpoint).status_code == 403
    actor.must_change_password = True
    assert client.get("/api/v3/orders/files").status_code == 403


def test_scan_history_pages_only_requested_events(db_session, register_client):
    from test_packing_lifecycle import setup_order

    from app.modules.packing.service import PackingPlanningService
    client, _ = register_client
    agent, _, _, order = setup_order(db_session)
    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)
    for index in range(65):
        db_session.add(AuditLog(event_type=f"CHANGE-{index:02}", actor_id=agent.id, actor_role=agent.role.value,
                                target_table="boxes", target_id=boxes[0].id))
    db_session.flush()
    code = boxes[0].qr_code_uid
    scan = client.get(f"/api/v3/packing/scan/{code}?include_history=false").json()
    assert scan["change_history"] == []
    page = client.get(f"/api/v3/packing/scan/{code}/history?offset=25").json()
    assert page["total"] >= 65 and len(page["items"]) == 25 and page["has_more"]
    summary = client.get("/api/v3/packing/orders").json()["items"][0]
    assert summary["boxes"] == len(boxes) and summary["outstanding"] == len(boxes)
    boxes[0].is_locked = True
    db_session.flush()
    assert client.get("/api/v3/packing/orders").json()["items"][0]["outstanding"] == len(boxes) - 1


def test_source_register_revalidates_workflow_eligibility(db_session, register_client):
    client, actor = register_client
    customer = Customer(client_code="SOURCE", company_name="Source Client", route_group="BKK", billing_email="unused", destination_country="TH")
    db_session.add(customer)
    db_session.flush()
    raw = make_file(db_session, actor, status=OrderStatus.PURCHASING_CONSOLIDATED, customer=customer)
    order = ClientOrder(raw_order_file_id=raw.id, customer_id=customer.id, order_ref_id="SOURCE-ORDER", status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(order)
    db_session.flush()
    assert client.get("/api/v3/procurement/source-files").json()["total"] == 1
    order.status = OrderStatus.HANDOFF_01_SENT
    db_session.flush()
    assert client.get("/api/v3/procurement/source-files").json()["total"] == 0


def test_master_invoice_search_includes_every_source_customer_date_and_route(db_session, register_client):
    from test_packing_lifecycle import setup_order

    from app.modules.packing.service import PackingPlanningService

    client, actor = register_client
    agent, _, _, first = setup_order(db_session)
    PackingPlanningService(db_session).design_order(order_id=first.id, actor=agent)
    second_customer = Customer(client_code="SECOND", company_name="Second Client", billing_email="unused", route_group="BE", destination_country="TH")
    db_session.add(second_customer)
    db_session.flush()
    raw = make_file(db_session, actor, customer=second_customer, day="2026-10-10")
    raw.route_line = "BE"
    second = ClientOrder(raw_order_file_id=raw.id, customer_id=second_customer.id, order_ref_id="SECOND-REF", status=OrderStatus.INVOICES_DRAFTED)
    first.status = OrderStatus.INVOICES_DRAFTED
    db_session.add(second)
    db_session.flush()
    invoice = Invoice(client_order_id=first.id, customer_id=first.customer_id, invoice_number="MASTER-INV", invoice_type="MASTER_SUMMARY", final_packing_manifest_id="manifest", subtotal_eur=10, total_amount_eur=10)
    db_session.add(invoice)
    db_session.flush()
    for order in (first, second):
        db_session.add(InvoiceLineItem(invoice_id=invoice.id, source_order_id=order.id, product_id=first.items[0].product_id,
            box_number=1, pack_size="1kg", billed_pack_count=1, billed_weight_kg=1, unit_price_eur=5, line_total_eur=5))
    db_session.flush()
    for params in [{"q": "SECOND-REF"}, {"customer": "Second Client"}, {"route": "BE"}, {"date_from": "2026-10-10", "date_to": "2026-10-10"}]:
        result = client.get("/api/v3/invoicing/records", params=params).json()
        assert result["total"] == 1 and "Second Client" in result["items"][0]["customer"]
        assert result["items"][0]["order_ref_id"] == "2 orders"
        assert "2026-10-10" in result["items"][0]["shipment_date"]
    assert client.get("/api/v3/invoicing/orders?view=completed").json()["total"] == 2


def test_register_index_migration_upgrades_and_downgrades(db_session):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration_path = Path(__file__).parents[1] / "alembic/versions/20261012_0011_operational_registers.py"
    spec = importlib.util.spec_from_file_location("register_indexes", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = db_session.connection()
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()  # Bootstrap/current-schema installs are safe to repeat.
        for table, name, _ in migration.INDEXES:
            assert name in {index["name"] for index in inspect(connection).get_indexes(table)}
        migration.downgrade()
        for table, name, _ in migration.INDEXES:
            assert name not in {index["name"] for index in inspect(connection).get_indexes(table)}
