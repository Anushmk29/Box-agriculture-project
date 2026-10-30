"""Quantity integrity across procurement and both packing rollout modes."""

import pytest
from fastapi.testclient import TestClient
from test_packing_lifecycle import setup_order
from test_procurement_handoff import setup_procurement_batch

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.main import app
from app.models.audit import AgentHandoff, AuditLog
from app.models.master_data import BoxCatalogue, PriceList, Product, ProductPackProfile
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus
from app.models.packing import Box, BoxItem
from app.models.procurement import PurchasingItem
from app.models.user import UserRole
from app.modules.packing.floor import FloorPackingService
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService
from app.services.on_behalf_invoicing_service import (
    MissingSellingPricesError,
    OnBehalfInvoicingService,
)


@pytest.mark.parametrize("packs,weight", [(10, 0), (0, 10), (1, 0.00001), (1, -1), (-1, 1)])
def test_contradictory_quantities_do_not_change_procurement(db_session, packs, weight):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    with pytest.raises(ValueError):
        ProcurementConfirmationService(db_session).confirm_and_dispatch(
            batch_id=batch.id, confirmations=[ProcurementConfirmation(item.id, packs, weight)],
            actor=buyer,
        )
    assert item.confirmed_purchased_pack_count is None
    assert item.confirmed_purchased_weight_kg is None
    assert batch.status == order.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert db_session.query(AgentHandoff).count() == db_session.query(AuditLog).count() == 0


def test_late_invalid_confirmation_does_not_partially_mutate_earlier_item(db_session):
    buyer, _, _, batch, first = setup_procurement_batch(db_session)
    second = PurchasingItem(purchasing_list_id=batch.id, product_id=first.product_id,
        pack_size="500g", ordered_net_weight_kg=1, total_required_pack_count=2,
        total_required_weight_kg=1)
    db_session.add(second)
    db_session.flush()
    db_session.expire(batch, ["items"])
    with pytest.raises(ValueError, match="both be zero or both be positive"):
        ProcurementConfirmationService(db_session).confirm_batch(batch_id=batch.id, actor=buyer,
            confirmations=[ProcurementConfirmation(first.id, 8, 8),
                           ProcurementConfirmation(second.id, 2, 0)])
    assert first.confirmed_purchased_pack_count is None
    assert second.confirmed_purchased_pack_count is None


def _packing_setup(db, monkeypatch, reviewed):
    _, _, actor, order = setup_order(db)
    actor.role = UserRole.OWNER_ADMIN
    product = order.items[0].product
    product.packing_reviewed = True
    product.firmness = "SOFT"
    db.query(BoxCatalogue).one().packing_reviewed = True
    db.add(ProductPackProfile(product_id=product.id, pack_size="1kg", pack_key="1000g",
                              net_weight_kg=1, volume_cm3=1000, reviewed=True))
    db.flush()
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", reviewed)
    return actor, order


@pytest.mark.parametrize("reviewed", [False, True])
def test_all_zero_template_rejected_before_boxes_or_handoffs(db_session, monkeypatch, reviewed):
    actor, order = _packing_setup(db_session, monkeypatch, reviewed)
    order.items[0].confirmed_pack_count = 0
    order.items[0].confirmed_net_weight_kg = 0
    db_session.flush()
    with pytest.raises(ValueError, match="At least one"):
        FloorPackingService(db_session).fast_track_template_pack_order(
            order.id, actor, packing_mode="NON_EL")
    assert db_session.query(Box).count() == db_session.query(AgentHandoff).count() == 0
    assert order.status == OrderStatus.HANDOFF_01_SENT


@pytest.mark.parametrize("reviewed", [False, True])
def test_mixed_template_skips_zero_and_preserves_fulfilled_quantity(
    db_session, monkeypatch, reviewed,
):
    actor, order = _packing_setup(db_session, monkeypatch, reviewed)
    zero = order.items[0]
    zero.pack_size = "500g"
    zero.confirmed_pack_count = zero.confirmed_net_weight_kg = 0
    db_session.add(ClientOrderItem(client_order_id=order.id, product_id=zero.product_id,
        raw_produce_text="Fulfilled", pack_size="1kg", ordered_pack_count=2,
        estimated_net_weight_kg=2, confirmed_pack_count=2, confirmed_net_weight_kg=2))
    db_session.flush()
    db_session.expire(order, ["items"])
    result = FloorPackingService(db_session).fast_track_template_pack_order(
        order.id, actor, packing_mode="NON_EL")
    assert result["total_net_weight_kg"] == 2
    packed = db_session.query(BoxItem).all()
    assert sum(item.final_packed_count for item in packed) == 2
    assert {item.pack_size for item in packed} == {"1kg"}


@pytest.mark.parametrize("reviewed", [False, True])
def test_positive_count_with_explicit_zero_weight_never_uses_template_fallback(
    db_session, monkeypatch, reviewed,
):
    actor, order = _packing_setup(db_session, monkeypatch, reviewed)
    order.items[0].confirmed_net_weight_kg = 0
    db_session.flush()
    with pytest.raises(ValueError, match="positive"):
        FloorPackingService(db_session).fast_track_template_pack_order(
            order.id, actor, packing_mode="NON_EL")
    assert db_session.query(Box).count() == 0


@pytest.mark.parametrize("packs,weight,expected", [(10, 8, 8), (7, 8, 7), (15, 15, 10),
                                                   (10, 8.99, 8), (0, 0, 0)])
def test_confirmed_allocation_preserves_standard_pack_weight(db_session, packs, weight, expected):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id, actor=buyer,
        confirmations=[ProcurementConfirmation(item.id, packs, weight)])
    assert order.items[0].confirmed_pack_count == expected
    assert float(order.items[0].confirmed_net_weight_kg) == expected
    assert float(order.items[0].confirmed_net_weight_kg) <= weight
    assert order.items[0].ordered_pack_count == 10
    contract = db_session.query(AgentHandoff).one().payload_json["confirmed_orders"][0]["items"][0]
    assert contract["shortage_pack_count"] == 10 - expected


def test_weight_limited_shortage_allocation_is_repeatable_across_customers(db_session):
    buyer, _, first, batch, item = setup_procurement_batch(db_session)
    first.items[0].ordered_pack_count = 5
    first.items[0].estimated_net_weight_kg = 5
    second = ClientOrder(raw_order_file_id=first.raw_order_file_id, customer_id=first.customer_id,
        order_ref_id="ORD-SECOND", status=OrderStatus.PURCHASING_CONSOLIDATED)
    db_session.add(second)
    db_session.flush()
    db_session.add(ClientOrderItem(client_order_id=second.id, product_id=item.product_id,
        raw_produce_text="Herb", pack_size="1kg", ordered_pack_count=5, estimated_net_weight_kg=5))
    db_session.flush()
    db_session.expire(first.raw_file, ["client_orders"])
    service = ProcurementConfirmationService(db_session)
    confirmation = [ProcurementConfirmation(item.id, 10, 7)]
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmation)
    counts = [first.items[0].confirmed_pack_count, second.items[0].confirmed_pack_count]
    assert counts == [4, 3]
    assert sum(float(order.items[0].confirmed_net_weight_kg) for order in [first, second]) == 7
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmation)
    assert [first.items[0].confirmed_pack_count, second.items[0].confirmed_pack_count] == counts
    assert db_session.query(AgentHandoff).count() == 1


@pytest.mark.parametrize("reviewed", [False, True])
def test_buyer_approved_substitute_reaches_packing_handoffs_and_invoice(
    db_session, monkeypatch, reviewed,
):
    buyer, _, order, batch, purchase = setup_procurement_batch(db_session)
    buyer.role = UserRole.OWNER_ADMIN
    original = purchase.product
    replacement = Product(t_code="SUBSTITUTE", name_en="Replacement herb", name_vi="Replacement",
        name_th="Replacement", box_material=original.box_material, temperature=original.temperature,
        control_status=original.control_status, produce_type=original.produce_type,
        weight_class=original.weight_class, packing_reviewed=True, firmness="SOFT")
    db_session.add(replacement)
    db_session.flush()
    db_session.add(ProductPackProfile(product_id=replacement.id, pack_size="1kg", pack_key="1000g",
        net_weight_kg=1, volume_cm3=1000, reviewed=True))
    db_session.add(BoxCatalogue(box_type=original.box_material, size_name="Substitute box",
        length_cm=30, width_cm=20, height_cm=24, rated_weight_kg=5,
        max_safe_net_weight_kg=5, volume_m3=0.0144, packing_reviewed=True))
    db_session.add(PriceList(product_id=original.id, pack_size="1kg", unit_price_eur=2,
                             valid_from="2026-01-01"))
    db_session.flush()
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", reviewed)
    service = ProcurementConfirmationService(db_session)
    confirmations = [ProcurementConfirmation(purchase.id, 4, 4,
                                              replacement_product_id=replacement.id)]
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmations)
    db_session.commit()
    db_session.expire_all()
    assert order.items[0].product_id == original.id
    assert order.items[0].replacement_product_id == replacement.id
    first = db_session.query(AgentHandoff).one()
    assert first.payload_json["confirmed_orders"][0]["items"][0]["product_id"] == replacement.id
    assert first.payload_json["confirmed_orders"][0]["items"][0]["ordered_product_id"] == original.id
    assert first.payload_json["confirmed_by_buyer"] == buyer.id
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmations)
    PackingPlanningService(db_session).prepare_order(order_id=order.id, actor=buyer, packing_mode="NON_EL")
    assert {item.product_id for item in db_session.query(BoxItem)} == {replacement.id}
    _, second = FinalPackingService(db_session).finalize_order(order_id=order.id, actor=buyer)
    assert second.payload_json["order_fulfillment"][0]["ordered_product_id"] == original.id
    assert second.payload_json["order_fulfillment"][0]["fulfilled_product_id"] == replacement.id
    billing = OnBehalfInvoicingService(db_session)
    with pytest.raises(MissingSellingPricesError) as missing:
        billing.generate_sub_customer_invoice(order.id, actor=buyer)
    assert missing.value.items[0]["product_id"] == replacement.id
    db_session.add(PriceList(product_id=replacement.id, pack_size="1kg", unit_price_eur=3.75,
                             valid_from="2026-01-01"))
    db_session.flush()
    invoice = billing.generate_sub_customer_invoice(order.id, actor=buyer)
    assert {line.product_id for line in invoice.items} == {replacement.id}
    assert float(invoice.total_amount_eur) == 15
    assert sum(line.billed_pack_count for line in invoice.items) == 4


@pytest.mark.parametrize("replacement", ["missing", "inactive", "original"])
def test_invalid_substitute_rejected_before_any_confirmation(db_session, replacement):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    replacement_id = "missing"
    if replacement == "original":
        replacement_id = item.product_id
    elif replacement == "inactive":
        product = Product(t_code="INACTIVE", name_en="Inactive", name_vi="Inactive", name_th="Inactive",
            box_material=item.product.box_material, temperature=item.product.temperature,
            control_status=item.product.control_status, produce_type=item.product.produce_type,
            weight_class=item.product.weight_class, is_active=False)
        db_session.add(product)
        db_session.flush()
        replacement_id = product.id
    with pytest.raises(ValueError):
        ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id, actor=buyer,
            confirmations=[ProcurementConfirmation(item.id, 4, 4, replacement_product_id=replacement_id)])
    assert item.confirmed_purchased_pack_count is None
    assert order.items[0].replacement_product_id is None
    assert db_session.query(AgentHandoff).count() == 0


def test_buyer_api_advertises_support_and_restores_approved_substitute(db_session):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    original = item.product
    replacement = Product(t_code="API-SUBSTITUTE", name_en="Actual herb", name_vi="Actual", name_th="Actual",
        box_material=original.box_material, temperature=original.temperature,
        control_status=original.control_status, produce_type=original.produce_type,
        weight_class=original.weight_class)
    db_session.add(replacement)
    db_session.commit()
    original_overrides = app.dependency_overrides.copy()

    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = transaction
    app.dependency_overrides[get_current_user] = lambda: buyer
    try:
        client = TestClient(app)
        url = f"/api/v3/procurement/batches/{batch.id}"
        assert client.get(url).json()["substitutions_supported"] is True
        response = client.put(url + "/confirm", json={"items": [{"item_id": item.id,
            "purchased_pack_count": 8, "purchased_weight_kg": 8,
            "replacement_product_id": replacement.id}]})
        assert response.status_code == 200, response.text
        reloaded = client.get(url).json()["items"][0]
        assert reloaded["product_id"] == original.id
        assert reloaded["replacement_product_id"] == replacement.id
        assert reloaded["replacement_product_code"] == "API-SUBSTITUTE"
        assert reloaded["replacement_product_name"] == "Actual herb"
        assert order.items[0].packing_product.id == replacement.id
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


@pytest.mark.parametrize("reviewed", [False, True])
def test_125g_standard_pack_weight_survives_handoff_packing_and_billing(db_session, monkeypatch, reviewed):
    buyer, _, order, batch, purchase = setup_procurement_batch(db_session)
    buyer.role = UserRole.OWNER_ADMIN
    line = order.items[0]
    line.pack_size = purchase.pack_size = "125g"
    line.ordered_pack_count = 1
    line.estimated_net_weight_kg = 0.125
    product = purchase.product
    product.packing_reviewed = True
    product.firmness = "SOFT"
    db_session.add(ProductPackProfile(product_id=product.id, pack_size="125g", pack_key="125g",
        net_weight_kg=0.125, volume_cm3=1000, reviewed=True))
    db_session.add(BoxCatalogue(box_type=product.box_material, size_name="Precision box",
        length_cm=30, width_cm=20, height_cm=24, rated_weight_kg=5,
        max_safe_net_weight_kg=5, volume_m3=0.0144, packing_reviewed=True))
    db_session.add(PriceList(product_id=product.id, pack_size="125g", unit_price_eur=2.5,
                             valid_from="2026-01-01"))
    db_session.flush()
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", reviewed)
    ProcurementConfirmationService(db_session).confirm_and_dispatch(batch_id=batch.id, actor=buyer,
        confirmations=[ProcurementConfirmation(purchase.id, 1, 0.125)])
    assert float(line.confirmed_net_weight_kg) == 0.125
    PackingPlanningService(db_session).prepare_order(order_id=order.id, actor=buyer, packing_mode="NON_EL")
    db_session.commit()
    db_session.expire_all()
    assert float(db_session.query(BoxItem).one().final_net_weight_kg) == 0.125
    FinalPackingService(db_session).finalize_order(order_id=order.id, actor=buyer)
    invoice = OnBehalfInvoicingService(db_session).generate_sub_customer_invoice(order.id, actor=buyer)
    db_session.commit()
    db_session.expire_all()
    assert float(invoice.items[0].billed_weight_kg) == 0.125


def test_floor_move_and_merge_preserve_125g_pack_weights(db_session):
    _, packer, _, order = setup_order(db_session)
    source = PackingPlanningService(db_session).design_order(order_id=order.id, actor=packer)[0]
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)
    source.items[0].pack_size = "125g"
    source.items[0].final_net_weight_kg = 0.5
    floor = FloorPackingService(db_session)
    target = floor.add_extra_box_to_order(order.id, source.box_catalogue_id, actor=packer)
    product_id = source.items[0].product_id
    floor.move_items_between_boxes(source.id, target.id, product_id, "125g", 1, actor=packer)
    db_session.commit()
    db_session.expire_all()
    assert float(db_session.query(BoxItem).filter_by(box_id=source.id).one().final_net_weight_kg) == 0.375
    assert float(db_session.query(BoxItem).filter_by(box_id=target.id).one().final_net_weight_kg) == 0.125
    floor.transfer_box_contents(target.id, source.id, actor=packer)
    db_session.commit()
    db_session.expire_all()
    assert float(db_session.query(BoxItem).filter_by(box_id=source.id).one().final_net_weight_kg) == 0.5


def test_decimal_purchase_confirmation_can_be_replayed_after_reload(db_session):
    from decimal import Decimal

    buyer, _, _, batch, item = setup_procurement_batch(db_session)
    service = ProcurementConfirmationService(db_session)
    confirmations = [ProcurementConfirmation(item.id, 9, Decimal("8.2100"))]
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmations)
    db_session.commit()
    db_session.expire_all()
    service.confirm_and_dispatch(batch_id=batch.id, actor=buyer, confirmations=confirmations)
    assert db_session.query(AgentHandoff).count() == 1
    assert item.confirmed_purchased_weight_kg == Decimal("8.2100")


def test_buffer_keeps_small_standard_weights_and_checks_derived_overflow(db_session):
    from decimal import Decimal

    from app.modules.procurement.batch import PurchasingBatchService

    item = PurchasingItem(ordered_net_weight_kg=Decimal("0.125"))
    PurchasingBatchService._apply_buffer(item, Decimal("10.00"), 1)
    assert item.buffer_weight_kg == Decimal("0.0125")
    assert item.total_required_weight_kg == Decimal("0.1375")
    assert item.total_required_pack_count == 2
    before = (item.buffer_percentage, item.total_required_pack_count, item.total_required_weight_kg)
    with pytest.raises(ValueError, match="supported range"):
        PurchasingBatchService._apply_buffer(item, 100, 2_147_483_647)
    assert (item.buffer_percentage, item.total_required_pack_count, item.total_required_weight_kg) == before
