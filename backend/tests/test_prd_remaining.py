"""PRD regression checks at the existing intake, procurement, packing and billing seams."""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from test_invoicing_invariant import build_order_with_box
from test_order_intake import _draft, _item, _setup
from test_packing_lifecycle import setup_order

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.invoice import Invoice, InvoiceStatus
from app.models.master_data import ClientSkuMapping, Product
from app.models.order import OrderStatus, RawOrderFile
from app.models.packing import Box
from app.models.procurement import MasterPurchasingList
from app.models.user import User, UserRole
from app.modules.orders.acknowledgment import acknowledgment_csv, prepare_acknowledgment
from app.modules.orders.intake import OrderIntake
from app.modules.orders.reconciliation import merge_products
from app.modules.orders.validation import OrderValidationService
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.modules.procurement.batch import PurchasingBatchService
from app.services.name_matcher import NameFirstMatcher


def api_client(db, actor):
    def transaction():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = transaction
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {create_access_token({'sub': actor.id})}"
    return client


def test_acknowledgment_reserves_real_order_ids_and_escapes_csv(db_session):
    raw, _ = _setup(db_session)
    drafts = [_draft(_item(3), client="CT-B"), _draft(_item(2), client="CT-A")]
    drafts[0].client_name = '=HYPERLINK("evil")'
    prepare_acknowledgment(raw, drafts)
    rows = list(csv.DictReader(io.StringIO(acknowledgment_csv(raw))))
    assert [row["Serial number"] for row in rows] == ["1", "2"]
    assert all(row["Total sub-customers"] == "2" for row in rows)
    assert rows[1]["Sub-customer name"].startswith("'=")
    orders = OrderIntake(db_session).materialize(raw_file=raw, drafts=drafts).orders
    assert {row["Order ID"] for row in rows} == {order.id for order in orders}
    assert {row["Order reference"] for row in rows} == {order.order_ref_id for order in orders}


def test_fuzzy_sku_requires_review_and_suggests_canonical_product(db_session):
    raw, product = _setup(db_session)
    draft = _draft(_item(2, name="Lemongras"))
    raw.acknowledgment_summary = {"drafts": [draft.model_dump(mode="json")]}
    actor = db_session.get(User, raw.uploaded_by)
    result = OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor)
    assert not result.can_split
    assert result.unresolved[0].suggestions[0]["id"] == product.id
    with pytest.raises(ValueError, match="Unresolved products"):
        OrderIntake(db_session).materialize(raw_file=raw, drafts=[draft])
    db_session.add(ClientSkuMapping(main_customer_id=raw.main_customer_id, client_raw_name="Lemongras", product_id=product.id, is_verified=True))
    db_session.flush()
    assert OrderValidationService(db_session).validate(raw_file_id=raw.id, actor=actor).can_split
    assert NameFirstMatcher(db_session).reviewed_product(["Lemongras"], main_customer_id="another-client") is None


def test_same_day_uploads_keep_separate_demand_and_buffers_after_rebuild(db_session):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    second = RawOrderFile(file_name="second.xlsx", storage_url="second.xlsx", shipment_target_date=raw.shipment_target_date,
        route_line=raw.route_line, main_customer_id=raw.main_customer_id, uploaded_by=actor.id, status=OrderStatus.PARSED)
    db_session.add(second)
    db_session.flush()
    intake = OrderIntake(db_session)
    first_order = intake.materialize(raw_file=raw, drafts=[_draft(_item(10))]).orders[0]
    second_order = intake.materialize(raw_file=second, drafts=[_draft(_item(20))]).orders[0]
    assert first_order.id != second_order.id and first_order.order_ref_id != second_order.order_ref_id
    raw.status = second.status = OrderStatus.SPLIT_AND_VALIDATED
    service = PurchasingBatchService(db_session)
    first_batch = service.consolidate_raw_file(raw.id, actor=actor)
    second_batch = service.consolidate_raw_file(second.id, actor=actor)
    assert first_batch.id != second_batch.id
    assert float(first_batch.items[0].ordered_net_weight_kg) == 1.0
    assert float(second_batch.items[0].ordered_net_weight_kg) == 2.0
    actor.role = UserRole.OWNER_ADMIN
    service.set_item_buffer(first_batch.items[0].id, 10, actor=actor, save_as_default=False)
    service.set_item_buffer(second_batch.items[0].id, 20, actor=actor, save_as_default=False)
    actor.role = UserRole.GENERAL
    service.rebuild_batch(first_batch.id, actor=actor)
    service.rebuild_batch(second_batch.id, actor=actor)
    assert db_session.query(MasterPurchasingList).count() == 2
    assert float(first_batch.items[0].ordered_net_weight_kg) == 1.0
    assert float(second_batch.items[0].ordered_net_weight_kg) == 2.0
    assert float(first_batch.items[0].buffer_percentage) == 10
    assert float(second_batch.items[0].buffer_percentage) == 20


def test_rebuild_keeps_historical_multi_file_batch_sources(db_session):
    from app.models.procurement import PurchasingOrderSource

    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    second = RawOrderFile(file_name="historical.xlsx", storage_url="historical.xlsx",
        shipment_target_date=raw.shipment_target_date, route_line=raw.route_line,
        uploaded_by=actor.id, main_customer_id=raw.main_customer_id, status=OrderStatus.PARSED)
    db_session.add(second)
    db_session.flush()
    for source, quantity in [(raw, 10), (second, 20)]:
        OrderIntake(db_session).materialize(raw_file=source, drafts=[_draft(_item(quantity))])
        source.status = OrderStatus.SPLIT_AND_VALIDATED
    batch = MasterPurchasingList(batch_reference="HISTORICAL", raw_order_file_id=raw.id,
        shipment_date=raw.shipment_target_date, total_skus_count=0, status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(batch)
    db_session.flush()
    for source in [raw, second]:
        db_session.add(PurchasingOrderSource(purchasing_list_id=batch.id, raw_order_file_id=source.id))
    db_session.flush()

    service = PurchasingBatchService(db_session)
    with pytest.raises(ValueError, match="one uploaded order file"):
        service._build_batch(raw_file_ids=[raw.id, second.id], actor=actor, batch_ref="NEW-MULTI-FILE")
    service.rebuild_batch(batch.id, actor=actor)
    assert db_session.query(MasterPurchasingList).count() == 1
    assert {source.raw_order_file_id for source in batch.sources} == {raw.id, second.id}
    assert float(batch.items[0].ordered_net_weight_kg) == 3.0


def test_new_batch_cannot_take_sources_from_another_batch(db_session):
    raw, _ = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    OrderIntake(db_session).materialize(raw_file=raw, drafts=[_draft(_item(10))])
    raw.status = OrderStatus.SPLIT_AND_VALIDATED
    db_session.flush()
    service = PurchasingBatchService(db_session)
    batch = service.consolidate_raw_file(raw.id, actor=actor)
    with pytest.raises(ValueError, match="another purchase batch"):
        service._build_batch(raw_file_ids=[raw.id], actor=actor, batch_ref="DIFFERENT-BATCH")
    assert db_session.query(MasterPurchasingList).count() == 1
    assert float(batch.items[0].ordered_net_weight_kg) == 1.0


def test_master_product_merge_collapses_pending_lines_and_remembers_alias(db_session):
    raw, target = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    actor.role = UserRole.OWNER_ADMIN
    source = Product(t_code="DUPLICATE", name_en="Lemongras", name_vi="Duplicate", name_th="Duplicate",
        box_material=target.box_material, temperature=target.temperature, control_status=target.control_status,
        produce_type=target.produce_type, weight_class=target.weight_class)
    db_session.add(source)
    db_session.flush()
    order = OrderIntake(db_session).materialize(raw_file=raw, drafts=[_draft(_item(2), _item(3, name="Lemongras"))]).orders[0]
    raw.status = OrderStatus.SPLIT_AND_VALIDATED
    batch = PurchasingBatchService(db_session).consolidate_raw_file(raw.id, actor=actor)
    merge_products(db_session, source_id=source.id, target_id=target.id, actor=actor)
    db_session.expire_all()
    assert not source.is_active
    assert [(line.product_id, line.ordered_pack_count) for line in order.items] == [(target.id, 5)]
    assert batch.items[0].product_id == target.id and float(batch.items[0].ordered_net_weight_kg) == 0.5
    assert NameFirstMatcher(db_session).reviewed_product(["Lemongras"]).id == target.id


def test_item_adjustments_finalize_with_derived_totals(db_session):
    agent, packer, _, order = setup_order(db_session)
    box = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)[0]
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)
    floor = FloorPackingService(db_session)
    item = box.items[0]
    order.packing_approval_status = "PENDING"
    floor.save_item(box.id, actor=packer, item_id=item.id, product_id=item.product_id, pack_size="1kg", quantity=3, net_weight_kg=3)
    assert box.actual_net_weight_kg is None and item.final_packed_count == 3
    FinalPackingService(db_session).finalize_order(order_id=order.id, actor=packer)
    assert box.is_locked and order.status == OrderStatus.HANDOFF_02_RETURNED
    assert float(box.actual_net_weight_kg) == 3
    assert float(box.actual_gross_weight_kg) == 3.5
    assert order.packing_approval_status == "PENDING"  # historical record is retained
    with pytest.raises(ValueError, match="locked and immutable"):
        floor.save_item(box.id, actor=packer, item_id=item.id, product_id=item.product_id, pack_size="1kg", quantity=2, net_weight_kg=2)


def test_transport_export_contains_only_locked_boxes_and_their_contents(db_session):
    order = build_order_with_box(db_session, locked=True)
    box = db_session.query(Box).filter_by(client_order_id=order.id).one()
    unlocked_order = build_order_with_box(db_session, locked=False, suffix="UNLOCKED", seller=order.main_customer)
    unlocked = db_session.query(Box).filter_by(client_order_id=unlocked_order.id).one()
    result = FloorPackingService(db_session).generate_vehicle_loading_export("TETE", "2026-09-10")
    rows = list(csv.DictReader(io.StringIO(result["csv_content"])))
    assert result["total_boxes_count"] == 1
    assert result["boxes"][0]["box_id"] == box.id
    assert rows[0]["SKU"] == box.items[0].product.t_code
    assert rows[0]["Packed Count"] == "2"
    assert unlocked.id not in result["csv_content"]


def test_bulk_invoices_are_separate_and_batch_approval_is_atomic(db_session):
    first = build_order_with_box(db_session, locked=True)
    actor = db_session.query(User).filter_by(email="agent1@example.com").one()
    second = build_order_with_box(db_session, locked=True, suffix="SECOND", seller=first.main_customer)
    actor.role = UserRole.OWNER_ADMIN
    actor.is_service_principal = False
    db_session.commit()
    client = api_client(db_session, actor)
    try:
        response = client.post("/api/v3/invoicing/generate-bulk", json={"order_ids": [first.id, second.id]})
        assert response.status_code == 200, response.text
        invoices = response.json()
        assert len(invoices) == 2 and len({row["invoice_number"] for row in invoices}) == 2
        assert all(row["invoice_type"] == "ON_BEHALF_SUB_CUSTOMER" for row in invoices)
        ids = [row["invoice_id"] for row in invoices]
        revisions = {row["invoice_id"]: row["revision"] for row in invoices}
        failed = client.post("/api/v3/invoicing/approve-bulk", json={"invoice_ids": [*ids, "not-found"], "expected_revisions": {**revisions, "not-found": 1}})
        assert failed.status_code == 404
        assert all(row.status == InvoiceStatus.INVOICE_DRAFT for row in db_session.query(Invoice).all())
        approved = client.post("/api/v3/invoicing/approve-bulk", json={"invoice_ids": ids, "expected_revisions": revisions})
        assert approved.status_code == 200
        assert all(row["status"] == "INVOICE_SENT" for row in approved.json())
    finally:
        app.dependency_overrides.clear()


def test_floor_api_cannot_edit_buffers_or_approve_packing(db_session):
    _, packer, _, order = setup_order(db_session)
    client = api_client(db_session, packer)
    try:
        assert client.put("/api/v3/procurement/items/not-found/buffer", json={"buffer_percentage": 10}).status_code == 403
        assert client.post(f"/api/v3/packing/orders/{order.id}/confirm-final-pack").status_code == 403
        assert client.get("/api/v3/packing/approval-queue").status_code == 403
        assert client.post("/api/v3/master-data/products", json={"t_code": "BUFFER-BYPASS", "name_en": "Bypass", "default_buffer_percentage": 10}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_acknowledgment_stays_in_platform_and_downloads_without_email(db_session):
    raw, _ = _setup(db_session)
    raw.acknowledgment_summary = {"delivery": {"status": "FAILED", "recipient": "old@example.test"}}
    prepare_acknowledgment(raw, [_draft(_item(3))])
    assert "delivery" not in raw.acknowledgment_summary
    db_session.commit()
    actor = db_session.get(User, raw.uploaded_by)
    client = api_client(db_session, actor)
    try:
        response = client.get(f"/api/v3/orders/intake-queue/{raw.id}")
        assert response.status_code == 200, response.text
        assert "delivery" not in response.json()
        report = client.get(f"/api/v3/orders/{raw.id}/acknowledgment")
        assert report.status_code == 200, report.text
        rows = list(csv.DictReader(io.StringIO(report.content.decode("utf-8-sig"))))
        assert len(rows) == 1
        assert rows[0]["Total sub-customers"] == "1"
        paths = app.openapi()["paths"]
        assert "/api/v3/orders/{raw_file_id}/acknowledgment/retry" not in paths
        assert "/api/v3/orders/{raw_file_id}/acknowledgment-status" not in paths
    finally:
        app.dependency_overrides.clear()



def test_master_merge_rejects_conflicting_prices_and_preserves_confirmed_lines(db_session):
    from app.models.master_data import PriceList
    raw, target = _setup(db_session)
    actor = db_session.get(User, raw.uploaded_by)
    actor.role = UserRole.OWNER_ADMIN
    source = Product(t_code="OLD", name_en="Old product", name_vi="Old", name_th="Old",
        box_material=target.box_material, temperature=target.temperature, control_status=target.control_status,
        produce_type=target.produce_type, weight_class=target.weight_class)
    db_session.add(source)
    db_session.flush()
    order = OrderIntake(db_session).materialize(raw_file=raw, drafts=[_draft(_item(2, name="Old product"))]).orders[0]
    order.status = OrderStatus.PROCUREMENT_CONFIRMED
    price = PriceList(product_id=source.id, pack_size="100g", unit_price_eur=2, valid_from="2026-01-01")
    db_session.add_all([price, PriceList(product_id=target.id, pack_size="100g", unit_price_eur=3, valid_from="2026-01-01")])
    db_session.flush()
    with pytest.raises(ValueError, match="prices conflict"):
        with db_session.begin_nested():
            merge_products(db_session, source_id=source.id, target_id=target.id, actor=actor)
    assert source.is_active and order.items[0].product_id == source.id
    price.unit_price_eur = 3
    db_session.flush()
    merge_products(db_session, source_id=source.id, target_id=target.id, actor=actor)
    assert not source.is_active and order.items[0].product_id == source.id
