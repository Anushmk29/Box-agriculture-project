"""Internal-only exception outcomes and the invoice-only application boundary."""
# ruff: noqa: F811
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_procurement_handoff import setup_procurement_batch
from test_system_resilience_audit import audit_client, ready_invoice  # noqa: F401

from app.models.audit import AgentHandoff
from app.models.invoice import Invoice, InvoiceLineItem, PaymentTransaction, Receipt
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus
from app.models.procurement import PurchasingItem
from app.models.request_result import RequestResult
from app.models.user import UserRole
from app.modules.procurement.batch import PurchasingBatchService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService


def submit(client, order, outcome="CANCELLED", key=None, **extra):
    return client.post(f"/api/v3/order-exceptions/{order.id}/resolve", headers={"Idempotency-Key": key or str(uuid4())}, json={
        "outcome": outcome, "reason": "Customer cancelled before buying", "expected_revision": order.packing_revision, **extra})


def shortage(db, amount=0):
    buyer, _, order, batch, item = setup_procurement_batch(db)
    ProcurementConfirmationService(db).confirm_and_dispatch(batch_id=batch.id,
        confirmations=[ProcurementConfirmation(item_id=item.id, purchased_pack_count=amount,
            purchased_weight_kg=amount, shortage_reason="Unavailable at market")], actor=buyer)
    db.commit()
    return order, batch


def test_staff_closes_confirmed_full_shortage_without_fabricating_work(audit_client, db_session):
    client, actor = audit_client
    order, _ = shortage(db_session)
    before = db_session.query(AgentHandoff).count()
    actor.role = UserRole.GENERAL
    db_session.commit()
    queue = client.get("/api/v3/order-exceptions").json()
    assert queue["total"] == 1 and queue["items"][0]["can_close_unavailable"]
    key = str(uuid4())
    original_revision = order.packing_revision
    response = submit(client, order, "UNAVAILABLE", key=key, reason="Nothing available; close today's order")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "UNAVAILABLE" and not response.json()["automatic_carry_forward"]
    retry = submit(client, order, "UNAVAILABLE", key=key, reason="Nothing available; close today's order", expected_revision=original_revision)
    assert retry.json() == response.json()
    assert db_session.query(ClientOrder).count() == 1
    assert db_session.query(Invoice).count() == 0 and db_session.query(AgentHandoff).count() == before
    assert client.get("/api/v3/packing/orders").json()["total"] == 0
    assert client.get("/api/v3/order-exceptions?view=completed").json()["total"] == 1
    assert client.get(f"/api/v3/order-exceptions/{order.id}").json()["resolution_reason"] == "Nothing available; close today's order"


@pytest.mark.parametrize("amount", [1, 8])
def test_partial_shortage_cannot_be_closed_as_unavailable(audit_client, db_session, amount):
    client, _ = audit_client
    order, _ = shortage(db_session, amount)
    assert submit(client, order, "UNAVAILABLE").status_code == 409
    assert order.status == OrderStatus.HANDOFF_01_SENT
    assert db_session.query(RequestResult).count() == 0


def test_unconfirmed_zero_is_not_full_shortage(audit_client, db_session):
    client, _ = audit_client
    _, _, order, _, _ = setup_procurement_batch(db_session)
    assert submit(client, order, "UNAVAILABLE").status_code == 409
    assert order.status == OrderStatus.PURCHASING_CONSOLIDATED


def test_cancelling_only_order_closes_empty_batch_and_keeps_original_demand(audit_client, db_session):
    client, actor = audit_client
    _, _, order, batch, _ = setup_procurement_batch(db_session)
    actor.role = UserRole.GENERAL
    db_session.commit()
    response = submit(client, order)
    assert response.status_code == 200, response.text
    assert order.status == batch.status == OrderStatus.CANCELLED
    assert db_session.query(ClientOrderItem).one().ordered_pack_count == 10
    assert db_session.query(PurchasingItem).count() == 0
    assert client.get("/api/v3/procurement/batches?view=pending").json()["total"] == 0
    assert client.get("/api/v3/procurement/source-files").json()["total"] == 0


def test_cancelling_one_order_recalculates_shared_purchase_and_remaining_order_dispatches(audit_client, db_session):
    client, actor = audit_client
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    sibling = ClientOrder(raw_order_file_id=order.raw_order_file_id, customer_id=order.customer_id,
        order_ref_id="KEEP-ORDER", status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(sibling)
    db_session.flush()
    db_session.add(ClientOrderItem(client_order_id=sibling.id, product_id=item.product_id,
        raw_produce_text="Keep demand", pack_size="1kg", ordered_pack_count=4, estimated_net_weight_kg=4))
    item.ordered_net_weight_kg = 14
    item.buffer_percentage = 10
    db_session.commit()
    response = submit(client, order)
    assert response.status_code == 200, response.text
    db_session.refresh(item)
    assert float(item.ordered_net_weight_kg) == 4 and item.total_required_pack_count == 5
    assert sibling.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert client.get("/api/v3/procurement/source-files").json()["total"] == 1
    ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id,
        confirmations=[ProcurementConfirmation(item_id=item.id, purchased_pack_count=4, purchased_weight_kg=4)], actor=buyer)
    db_session.commit()
    assert order.status == OrderStatus.CANCELLED and sibling.status == OrderStatus.HANDOFF_01_SENT
    assert sibling.items[0].confirmed_pack_count == 4


def test_later_cancellation_requires_admin_and_explicit_manual_confirmation(audit_client, db_session):
    client, actor = audit_client
    order, batch = shortage(db_session, 8)
    actor.role = UserRole.GENERAL
    db_session.commit()
    assert submit(client, order).status_code == 409
    assert submit(client, order, manual_resolution_confirmed=True).status_code == 403
    actor.role = UserRole.OWNER_ADMIN
    db_session.commit()
    assert submit(client, order).status_code == 409
    response = submit(client, order, manual_resolution_confirmed=True, reason="Goods reassigned manually by admin")
    assert response.status_code == 200, response.text
    assert batch.status == OrderStatus.HANDOFF_01_SENT
    assert batch.items[0].confirmed_purchased_pack_count == 8
    assert order.items[0].confirmed_pack_count == 8  # Historical physical evidence stays intact.
    assert list(PurchasingBatchService(db_session).ordered_packs(batch).values()) == [10]


def test_stale_resolution_and_invalid_reason_leave_everything_unchanged(audit_client, db_session):
    client, _ = audit_client
    _, _, order, batch, item = setup_procurement_batch(db_session)
    assert submit(client, order, expected_revision=99).status_code == 409
    assert submit(client, order, reason="   ").status_code == 422
    assert order.status == batch.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert db_session.query(RequestResult).count() == 0
    assert db_session.get(PurchasingItem, item.id) is not None


@pytest.mark.parametrize("secondary", [False, True])
def test_existing_direct_or_master_invoice_blocks_cancellation(audit_client, db_session, secondary):
    client, actor = audit_client
    service, invoice = ready_invoice(db_session, actor)
    order = db_session.get(ClientOrder, invoice.client_order_id)
    if secondary:
        other = ClientOrder(raw_order_file_id=order.raw_order_file_id, customer_id=order.customer_id,
            order_ref_id="MASTER-ANCHOR", status=OrderStatus.INVOICES_DRAFTED)
        db_session.add(other)
        db_session.flush()
        invoice.client_order_id = other.id
        db_session.query(InvoiceLineItem).filter_by(invoice_id=invoice.id).update({"source_order_id":order.id})
    db_session.commit()
    assert submit(client, order, manual_resolution_confirmed=True).status_code == 409
    assert db_session.query(Invoice).count() == 1


@pytest.mark.parametrize("method,suffix", [("post", "record-payment"), ("get", "receipt"), ("post", "close-order")])
def test_post_invoice_money_and_receipt_routes_are_not_exposed(audit_client, db_session, method, suffix):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    response = getattr(client, method)(f"/api/v3/invoicing/{invoice.id}/{suffix}")
    assert response.status_code == 404
    assert db_session.query(PaymentTransaction).count() == db_session.query(Receipt).count() == 0


def test_final_invoice_finishes_application_work_without_payment(audit_client, db_session):
    client, actor = audit_client
    _, invoice = ready_invoice(db_session, actor)
    response = client.post(f"/api/v3/invoicing/{invoice.id}/finalize", json={"expected_revision":invoice.revision})
    assert response.status_code == 200, response.text
    summary = client.get("/api/v3/operations/summary").json()
    assert summary["awaiting_billing"] == 0 and summary["invoiced_orders"] == 1
    assert "open_balances_by_currency" not in summary
    assert db_session.query(PaymentTransaction).count() == db_session.query(Receipt).count() == 0
    assert client.get(f"/api/v3/invoicing/{invoice.id}/html").status_code == 200


def test_outcome_migration_preserves_history_and_rejects_used_downgrade():
    path = Path(__file__).resolve().parents[1]/"alembic/versions/20261027_0023_order_exception_outcomes.py"
    spec = importlib.util.spec_from_file_location("order_outcomes", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for table in ("client_orders", "raw_order_files", "master_purchasing_lists"):
            conn.exec_driver_sql(f"CREATE TABLE {table} (id TEXT PRIMARY KEY,status VARCHAR(32))")
            conn.exec_driver_sql(f"INSERT INTO {table} VALUES ('kept','SPLIT_AND_VALIDATED')")
        migration.upgrade()
        migration.upgrade()
        migration.downgrade()
        conn.exec_driver_sql("UPDATE client_orders SET status='UNAVAILABLE'")
        with pytest.raises(RuntimeError, match="used order outcomes"):
            migration.downgrade()
        assert conn.exec_driver_sql("SELECT count(*) FROM client_orders").scalar() == 1
