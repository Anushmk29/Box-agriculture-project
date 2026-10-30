"""Run with BOX_TEST_POSTGRES_URL pointing at a disposable local PostgreSQL DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker
from test_invoicing_invariant import build_order_with_box
from test_order_intake import _setup as intake_setup
from test_packing_lifecycle import setup_order
from test_procurement_handoff import setup_procurement_batch

from app.api.invoicing import RecordPaymentRequest, record_payment_and_generate_receipt
from app.api.orders import AdHocOrderRequest, add_adhoc_order
from app.core.database import Base
from app.core.revisions import RevisionConflict, lock_invoice, lock_packing
from app.models.audit import AgentHandoff
from app.models.invoice import Invoice, PaymentTransaction
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.packing import Box
from app.models.request_result import RequestResult
from app.models.user import User, UserRole
from app.modules.orders.exceptions import resolve_order
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService
from app.services.on_behalf_invoicing_service import OnBehalfInvoicingService


def test_postgres_independent_planners_share_catalogue_without_blocking(postgres_sessions):
    from app.modules.packing.planning import lock_inputs
    sessions = postgres_sessions
    with sessions() as db:
        _, _, _, order = setup_order(db)
        order_id = order.id
        db.commit()
    with sessions() as first, sessions() as second:
        lock_inputs(first, first.get(ClientOrder, order_id))
        second.execute(text("SET LOCAL lock_timeout='100ms'"))
        lock_inputs(second, second.get(ClientOrder, order_id))


def test_postgres_relevant_configuration_writer_is_blocked_but_unrelated_product_is_not(postgres_sessions):
    from sqlalchemy.exc import OperationalError

    from app.models.master_data import Product
    from app.modules.packing.planning import lock_inputs
    sessions = postgres_sessions
    with sessions() as db:
        _, _, _, order = setup_order(db)
        order_id, product_id = order.id, order.items[0].product_id
        product = order.items[0].product
        other = Product(t_code="UNRELATED", name_en="Other", name_vi="Other", name_th="Other",
            box_material=product.box_material, temperature=product.temperature,
            control_status=product.control_status, produce_type=product.produce_type, weight_class=product.weight_class)
        db.add(other)
        db.commit()
        other_id = other.id
    with sessions() as planner, sessions() as writer:
        lock_inputs(planner, planner.get(ClientOrder, order_id))
        writer.execute(text("SET LOCAL lock_timeout='100ms'"))
        assert writer.query(Product).filter_by(id=other_id).with_for_update().one()
        with pytest.raises(OperationalError) as error:
            writer.query(Product).filter_by(id=product_id).with_for_update().one()
        assert error.value.orig.pgcode == "55P03"


def test_postgres_request_timeouts_are_local_and_expiry_does_not_poison_reused_connection(postgres_sessions):
    from time import monotonic

    from sqlalchemy.exc import DBAPIError
    sessions = postgres_sessions
    with sessions() as db:
        db.info["request_deadline"] = monotonic() + 45
        values = dict(db.execute(text("SELECT name, setting FROM pg_settings WHERE name IN ('lock_timeout','statement_timeout')")).all())
        assert 0 < int(values["lock_timeout"]) <= 3000
        assert 0 < int(values["statement_timeout"]) <= 15000
        db.execute(text("SET LOCAL statement_timeout='20ms'"))
        with pytest.raises(DBAPIError) as error:
            db.execute(text("SELECT pg_sleep(0.1)"))
        assert error.value.orig.pgcode == "57014"
        db.rollback()
    with sessions() as fresh:
        assert fresh.scalar(text("SELECT 1")) == 1
        assert fresh.scalar(text("SELECT current_setting('statement_timeout')")) != "20ms"


@pytest.mark.parametrize("commits", [False, True])
def test_postgres_cleanup_cannot_delete_an_inflight_source(postgres_sessions, tmp_path, monkeypatch, commits):
    from datetime import UTC, datetime, timedelta
    from io import BytesIO

    from app.core.config import settings
    from app.core.file_storage import LocalStorage, persist_source, stage_upload
    from app.modules.orders.source_lifecycle import reconcile_orphans

    sessions = postgres_sessions
    monkeypatch.setattr(settings, "STORAGE_GC_ENABLED", True)
    store = LocalStorage(tmp_path / "objects")
    with sessions() as db:
        raw, _ = intake_setup(db)
        raw_id = raw.id
    ready, finish = Event(), Event()
    def writer():
        with sessions() as db, stage_upload(BytesIO(b"fixture"), "source.xlsx") as content:
            raw = db.get(RawOrderFile, raw_id)
            raw.storage_url = persist_source(db, content, store=store)
            raw.file_sha256, raw.file_size_bytes = content.sha256, content.size
            path = store._path(store.key(raw.storage_url))
            stamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
            os.utime(path, (stamp, stamp))
            ready.set()
            assert finish.wait(timeout=20)
            db.commit() if commits else db.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        try:
            assert ready.wait(timeout=10)
            with sessions() as db:
                result = reconcile_orphans(db, store=store, apply=True)
                assert result["busy"] == 1 and not result["deleted"]
        finally:
            finish.set()
        future.result(timeout=30)
    with sessions() as db:
        result = reconcile_orphans(db, store=store, apply=True)
        assert len(result["deleted"]) == (0 if commits else 1)
        assert result["referenced"] == (1 if commits else 0)


def test_postgres_same_upload_key_has_one_document_and_one_result(postgres_sessions, tmp_path, monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile
    from test_source_storage import workbook_bytes

    from app.api.orders import upload_order_matrix
    from app.core.config import settings

    sessions = postgres_sessions
    monkeypatch.setattr(settings, "LOCAL_STORAGE_ROOT", str(tmp_path / "objects"))
    with sessions() as db:
        raw, _ = intake_setup(db)
        actor_id, main_id = raw.uploaded_by, raw.main_customer_id
    content, key = workbook_bytes(), str(uuid4())
    def upload(db, actor):
        return upload_order_matrix(file=UploadFile(filename="orders.xlsx", file=BytesIO(content)),
            shipment_date="2026-09-10", route_line="CT", main_customer_id=main_id,
            request_key=key, db=db, actor=actor)
    results = concurrent(sessions, actor_id, upload)
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(RequestResult).count() == 1
        assert db.query(RawOrderFile).count() == 2
    assert len(list((tmp_path / "objects").rglob("*.xlsx"))) == 1


@pytest.fixture
def postgres_sessions():
    url = os.getenv("BOX_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("BOX_TEST_POSTGRES_URL is required for disposable PostgreSQL tests")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or parsed.database != "box_tests":
        pytest.fail("Concurrency tests require a local disposable database named box_tests")
    schema = "box_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def concurrent(sessions, actor_id, operation):
    barrier = Barrier(2)
    def worker():
        with sessions() as db:
            actor = db.get(User, actor_id)
            barrier.wait(timeout=10)
            result = operation(db, actor)
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        return [future.result(timeout=30) for future in futures]


@pytest.mark.parametrize("distinct_intents", [False, True])
def test_postgres_adhoc_replays_and_distinct_additions_are_serialized(postgres_sessions, distinct_intents):
    sessions = postgres_sessions
    with sessions() as db:
        raw, product = intake_setup(db)
        actor_id, raw_id, code = raw.uploaded_by, raw.id, product.t_code
    shared_key = str(uuid4())
    def add(db, actor):
        return add_adhoc_order(raw_id, AdHocOrderRequest(client_code="CT-A", items=[{"t_code": code, "pack_count": 2}]),
            request_key=str(uuid4()) if distinct_intents else shared_key, db=db, actor=actor)
    results = concurrent(sessions, actor_id, add)
    if not distinct_intents:
        assert results[0] == results[1]
    with sessions() as db:
        assert db.query(ClientOrder).one().total_ordered_items_count == (4 if distinct_intents else 2)
        assert db.query(ClientOrderItem).count() == db.query(RequestResult).count() == (2 if distinct_intents else 1)


def test_postgres_same_payment_key_creates_one_ledger_entry(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        order = build_order_with_box(db, locked=True)
        actor = db.query(User).filter_by(email="agent1@example.com").one()
        actor.role, actor.is_service_principal = UserRole.OWNER_ADMIN, False
        service = OnBehalfInvoicingService(db)
        invoice = service.generate_sub_customer_invoice(order.id, actor=actor)
        service.approve_and_dispatch(invoice_id=invoice.id, actor=actor)
        db.commit()
        actor_id, invoice_id = actor.id, invoice.id
    key = str(uuid4())
    def pay(db, actor):
        return record_payment_and_generate_receipt(invoice_id, RecordPaymentRequest(amount_paid=1,
            payment_date="2026-10-27", bank_name="Bank", bank_wire_reference="RACE-ONE"),
            request_key=key, db=db, actor=actor)
    results = concurrent(sessions, actor_id, pay)
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(PaymentTransaction).count() == db.query(RequestResult).count() == 1


def test_postgres_concurrent_preparation_creates_one_box_set(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        _, actor, _, order = setup_order(db)
        actor_id, order_id = actor.id, order.id
    def prepare(db, actor):
        PackingPlanningService(db).prepare_order(order_id=order_id, actor=actor)
        return [box.id for box in db.query(Box).filter_by(client_order_id=order_id)]
    results = concurrent(sessions, actor_id, prepare)
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(Box).count() == 1


def test_postgres_concurrent_confirmation_creates_one_handoff(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        actor, _, _, batch, item = setup_procurement_batch(db)
        db.commit()
        actor_id, batch_id, item_id = actor.id, batch.id, item.id
    def confirm(db, actor):
        ProcurementConfirmationService(db).confirm_and_dispatch(batch_id=batch_id,
            confirmations=[ProcurementConfirmation(item_id, 11, 11)], actor=actor)
        return db.query(AgentHandoff).one().id
    results = concurrent(sessions, actor_id, confirm)
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(AgentHandoff).count() == 1


def test_postgres_concurrent_finalization_reuses_handoff(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        _, actor, _, order = setup_order(db)
        planning = PackingPlanningService(db)
        planning.prepare_order(order_id=order.id, actor=actor)
        db.commit()
        actor_id, order_id = actor.id, order.id
    def finalize(db, actor):
        return FinalPackingService(db).finalize_order(order_id=order_id, actor=actor)[1].id
    results = concurrent(sessions, actor_id, finalize)
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(AgentHandoff).count() == 1
        assert all(box.is_locked for box in db.query(Box))


def test_postgres_two_observed_invoice_edits_cannot_both_save(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        order = build_order_with_box(db, locked=True)
        actor = db.query(User).filter_by(email="agent1@example.com").one()
        actor.role, actor.is_service_principal = UserRole.OWNER_ADMIN, False
        invoice = OnBehalfInvoicingService(db).generate_sub_customer_invoice(order.id, actor=actor)
        db.commit()
        actor_id, invoice_id, revision = actor.id, invoice.id, invoice.revision
    def edit(db, actor):
        try:
            invoice = lock_invoice(db, invoice_id, revision)
            OnBehalfInvoicingService(db).update_draft(invoice_id=invoice.id, actor=actor,
                line_prices={item.id: 3 for item in invoice.items}, internal_notes="Reviewed")
            return "saved"
        except RevisionConflict:
            db.rollback()
            return "conflict"
    assert sorted(concurrent(sessions, actor_id, edit)) == ["conflict", "saved"]
    with sessions() as db:
        invoice = db.get(Invoice, invoice_id)
        assert invoice.items[0].unit_price_eur == 3
        assert invoice.revision > revision


def test_postgres_finalization_and_observed_edit_have_one_winner(postgres_sessions):
    sessions = postgres_sessions
    with sessions() as db:
        _, actor, _, order = setup_order(db)
        PackingPlanningService(db).prepare_order(order_id=order.id, actor=actor)
        db.commit()
        box = db.query(Box).filter_by(client_order_id=order.id).one()
        item = box.items[0]
        actor_id, order_id, box_id, revision = actor.id, order.id, box.id, order.packing_revision
        item_id, product_id, pack_size = item.id, item.product_id, item.pack_size
    barrier = Barrier(2)
    def worker(finalize):
        with sessions() as db:
            actor = db.get(User, actor_id)
            barrier.wait(timeout=10)
            try:
                lock_packing(db, order_id, revision)
                if finalize:
                    FinalPackingService(db).finalize_order(order_id=order_id, actor=actor)
                else:
                    FloorPackingService(db).save_item(box_id, actor=actor, item_id=item_id,
                        product_id=product_id, pack_size=pack_size, quantity=3, net_weight_kg=3)
                db.commit()
                return "finalized" if finalize else "edited"
            except RevisionConflict:
                db.rollback()
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, finalize) for finalize in (True, False)]
        results = [future.result(timeout=30) for future in futures]
    assert results.count("conflict") == 1
    with sessions() as db:
        box = db.get(Box, box_id)
        assert box.is_locked == ("finalized" in results)
        assert box.items[0].final_packed_count == (4 if box.is_locked else 3)


@pytest.mark.parametrize("cancel_first", [True, False])
def test_postgres_cancellation_and_purchase_confirmation_serialize(postgres_sessions, cancel_first):
    sessions = postgres_sessions
    with sessions() as db:
        actor, _, order, batch, item = setup_procurement_batch(db)
        actor_id, order_id, batch_id, item_id, revision = actor.id, order.id, batch.id, item.id, order.packing_revision
        db.commit()
    def perform(db, cancellation):
        actor = db.get(User, actor_id)
        if cancellation:
            return resolve_order(db, order_id=order_id, outcome=OrderStatus.CANCELLED,
                reason="Customer cancelled before buying", expected_revision=revision, manual=False, actor=actor)
        return ProcurementConfirmationService(db).confirm_and_dispatch(batch_id=batch_id,
            confirmations=[ProcurementConfirmation(item_id, 8, 8)], actor=actor)
    waiting = Event()
    def second():
        with sessions() as db:
            waiting.set()
            try:
                perform(db, not cancel_first)
                db.commit()
                return "unexpected-success"
            except (ValueError, RevisionConflict):
                db.rollback()
                return "rejected"
    with sessions() as first, ThreadPoolExecutor(max_workers=1) as pool:
        perform(first, cancel_first)
        future = pool.submit(second)
        assert waiting.wait(5)
        first.commit()
        assert future.result(timeout=15) == "rejected"
    with sessions() as db:
        order = db.get(ClientOrder, order_id)
        assert order.status == (OrderStatus.CANCELLED if cancel_first else OrderStatus.HANDOFF_01_SENT)
        assert db.query(AgentHandoff).count() == (0 if cancel_first else 1)
