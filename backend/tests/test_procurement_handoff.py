import hashlib
import json

import pytest

from app.models.audit import AgentHandoff, AuditLog
from app.models.master_data import (
    BoxCatalogue,
    BoxMaterial,
    ControlStatus,
    Customer,
    ProduceType,
    Product,
    TemperatureZone,
    WeightClass,
)
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.models.procurement import MasterPurchasingList, PurchasingItem, PurchasingOrderSource
from app.models.user import User, UserRole
from app.modules.handoffs.service import HandoffService
from app.modules.procurement.batch import PurchasingBatchService
from app.modules.procurement.service import ProcurementConfirmation, ProcurementConfirmationService


def setup_procurement_batch(db_session):
    buyer = User(
        email="buyer@example.com",
        password_hash="unused",
        full_name="Market Buyer",
        role=UserRole.GENERAL,
    )
    agent = User(
        email="agent1@example.com",
        password_hash="unused",
        full_name="Agent One",
        role=UserRole.AI_AGENT_1,
        is_service_principal=True,
    )
    customer = Customer(
        client_code="BUYER-1",
        company_name="Buyer One",
        billing_email="buyer1@example.com",
        route_group="TETE",
        destination_country="France",
    )
    product = Product(
        t_code="T001",
        name_vi="Rau",
        name_en="Herb",
        name_th="Pak",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["1kg"],
    )
    db_session.add_all([buyer, agent, customer, product])
    db_session.flush()
    raw_file = RawOrderFile(
        file_name="order.xlsx",
        storage_url="order.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.PURCHASING_CONSOLIDATED,
    )
    db_session.add(raw_file)
    db_session.flush()
    order = ClientOrder(
        raw_order_file_id=raw_file.id,
        customer_id=customer.id,
        order_ref_id="ORD-20260910-BUYER1",
        total_ordered_items_count=10,
        total_estimated_net_weight_kg=10,
        status=OrderStatus.PURCHASING_CONSOLIDATED,
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        ClientOrderItem(
            client_order_id=order.id,
            product_id=product.id,
            raw_produce_text="T001 - Herb",
            pack_size="1kg",
            ordered_pack_count=10,
            estimated_net_weight_kg=10,
        )
    )
    batch = MasterPurchasingList(
        raw_order_file_id=raw_file.id,
        batch_reference="PUR-20260910-1",
        shipment_date="2026-09-10",
        route_code="TETE",
        total_skus_count=1,
        status=OrderStatus.PURCHASING_CONSOLIDATED,
    )
    db_session.add(batch)
    db_session.flush()
    source = PurchasingOrderSource(
        purchasing_list_id=batch.id,
        raw_order_file_id=raw_file.id,
    )
    item = PurchasingItem(
        purchasing_list_id=batch.id,
        product_id=product.id,
        pack_size="1kg",
        ordered_net_weight_kg=10,
        total_required_pack_count=11,
        total_required_weight_kg=11,
    )
    db_session.add_all([source, item])
    db_session.commit()
    return buyer, agent, order, batch, item


def test_procurement_confirmation_is_audited_human_gate(db_session):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)

    ProcurementConfirmationService(db_session).confirm_batch(
        batch_id=batch.id,
        confirmations=[
            ProcurementConfirmation(
                item_id=item.id,
                purchased_pack_count=8,
                purchased_weight_kg=8.0,
                shortage_reason="Market shortage",
            )
        ],
        actor=buyer,
    )

    assert batch.status == OrderStatus.PROCUREMENT_CONFIRMED
    assert order.status == OrderStatus.PROCUREMENT_CONFIRMED
    assert item.confirmed_purchased_pack_count == 8
    assert item.shortage_reason == "Market shortage"
    assert db_session.query(AuditLog).filter_by(event_type="PROCUREMENT_CONFIRMED").count() == 1


def test_handoff_01_is_canonical_hashed_and_advances_orders(db_session):
    buyer, agent, order, batch, item = setup_procurement_batch(db_session)
    ProcurementConfirmationService(db_session).confirm_batch(
        batch_id=batch.id,
        confirmations=[ProcurementConfirmation(item.id, 8, 8.0, "Market shortage")],
        actor=buyer,
    )

    handoff = HandoffService(db_session).dispatch_handoff_01(batch_id=batch.id, actor=agent)

    assert handoff.handoff_type == "HANDOFF_01_ORDER_DATA"
    assert len(handoff.payload_sha256) == 64
    assert handoff.payload_json["procurement_batch_id"] == batch.id
    assert handoff.payload_json["confirmed_orders"][0]["items"][0]["confirmed_pack_count"] == 8
    assert order.items[0].confirmed_pack_count == 8
    assert float(order.items[0].confirmed_net_weight_kg) == 8.0
    assert order.status == OrderStatus.HANDOFF_01_SENT
    assert db_session.query(AgentHandoff).count() == 1

    handoff_id = handoff.id
    db_session.expire_all()
    persisted = db_session.query(AgentHandoff).filter_by(id=handoff_id).one()
    encoded = json.dumps(
        persisted.payload_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert persisted.payload_json["handoff_id"] == handoff_id
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == persisted.payload_sha256


def test_consolidation_advances_every_source_order(db_session):
    buyer, agent, order, _, _ = setup_procurement_batch(db_session)
    del agent
    order.status = OrderStatus.SPLIT_AND_VALIDATED
    order.raw_file.status = OrderStatus.SPLIT_AND_VALIDATED
    db_session.query(MasterPurchasingList).delete()
    db_session.query(PurchasingOrderSource).delete()
    db_session.query(PurchasingItem).delete()
    db_session.commit()

    batch = PurchasingBatchService(db_session).consolidate_raw_file(order.raw_order_file_id, actor=buyer)

    assert batch.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert order.raw_file.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert order.status == OrderStatus.PURCHASING_CONSOLIDATED
    assert all(item.confirmed_purchased_pack_count is None for item in batch.items)
    assert all(item.confirmed_purchased_weight_kg is None for item in batch.items)


def test_handoff_requires_confirmation_and_rejects_duplicate_dispatch(db_session):
    buyer, agent, order, batch, item = setup_procurement_batch(db_session)
    service = HandoffService(db_session)
    with pytest.raises(ValueError, match="human-confirmed"):
        service.dispatch_handoff_01(batch_id=batch.id, actor=agent)
    assert db_session.query(AgentHandoff).count() == 0

    ProcurementConfirmationService(db_session).confirm_batch(
        batch_id=batch.id,
        confirmations=[ProcurementConfirmation(item.id, 8, 8.0)],
        actor=buyer,
    )
    buyer.role = UserRole.GENERAL_ACCOUNTING
    with pytest.raises(PermissionError):
        service.dispatch_handoff_01(batch_id=batch.id, actor=buyer)
    buyer.role = UserRole.OWNER_ADMIN
    service.dispatch_handoff_01(batch_id=batch.id, actor=buyer)
    with pytest.raises(ValueError, match="human-confirmed"):
        service.dispatch_handoff_01(batch_id=batch.id, actor=buyer)
    assert db_session.query(AgentHandoff).count() == 1

    from app.modules.packing.service import PackingPlanningService

    item.product.default_unit_volume_cm3 = 100
    db_session.add(BoxCatalogue(
        box_type=BoxMaterial.FOAM, size_name="Handoff test box", max_safe_net_weight_kg=10,
        volume_m3=0.047628, length_cm=54, width_cm=42, height_cm=21, rated_weight_kg=10,
    ))
    db_session.flush()
    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=buyer)
    assert boxes
    assert sum(line.final_packed_count for box in boxes for line in box.items) == 8


def test_handoff_01_ignores_zero_quantity_order_rows(db_session):
    buyer, agent, order, batch, item = setup_procurement_batch(db_session)
    zero_product = Product(
        t_code="T-ZERO",
        name_vi="Không đặt",
        name_en="Zero quantity",
        name_th="ไม่มี",
        box_material=BoxMaterial.FOAM,
        temperature=TemperatureZone.COLD,
        control_status=ControlStatus.COMBINABLE,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        default_pack_sizes=["100g"],
    )
    db_session.add(zero_product)
    db_session.flush()
    db_session.add(
        ClientOrderItem(
            client_order_id=order.id,
            product_id=zero_product.id,
            raw_produce_text="T-ZERO - Zero quantity",
            pack_size="100g",
            ordered_pack_count=0,
            estimated_net_weight_kg=0,
        )
    )
    zero_purchase = PurchasingItem(
        purchasing_list_id=batch.id,
        product_id=zero_product.id,
        pack_size="100g",
        ordered_net_weight_kg=0,
        total_required_pack_count=0,
        total_required_weight_kg=0,
    )
    db_session.add(zero_purchase)
    db_session.commit()

    ProcurementConfirmationService(db_session).confirm_batch(
        batch_id=batch.id,
        confirmations=[
            ProcurementConfirmation(item.id, 8, 8.0, "Market shortage"),
            ProcurementConfirmation(zero_purchase.id, 0, 0.0),
        ],
        actor=buyer,
    )

    handoff = HandoffService(db_session).dispatch_handoff_01(batch_id=batch.id, actor=agent)

    signed_items = handoff.payload_json["confirmed_orders"][0]["items"]
    assert all(signed_item["product_code"] != "T-ZERO" for signed_item in signed_items)

    # Rows with no order quantity are intentionally absent from Handoff 01.
    # Packing must accept that same order without requiring their confirmation.
    from app.modules.packing.service import PackingPlanningService

    buyer.role = UserRole.OWNER_ADMIN
    item.product.default_unit_volume_cm3 = 100
    db_session.add(BoxCatalogue(
        box_type=BoxMaterial.FOAM, size_name="Zero row regression box", max_safe_net_weight_kg=10,
        volume_m3=0.047628, length_cm=54, width_cm=42, height_cm=21, rated_weight_kg=10,
    ))
    db_session.flush()
    db_session.expire_all()
    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=buyer)
    assert boxes
    assert sum(line.final_packed_count for box in boxes for line in box.items) == 8
    assert all(line.product_id != zero_product.id for box in boxes for line in box.items)


@pytest.mark.parametrize("missing_field", ["confirmed_pack_count", "confirmed_net_weight_kg"])
def test_packing_still_requires_confirmation_for_positive_order_rows(db_session, missing_field):
    from app.modules.packing.service import PackingPlanningService

    buyer, agent, order, batch, item = setup_procurement_batch(db_session)
    ProcurementConfirmationService(db_session).confirm_batch(
        batch_id=batch.id,
        confirmations=[ProcurementConfirmation(item.id, 8, 8.0)],
        actor=buyer,
    )
    HandoffService(db_session).dispatch_handoff_01(batch_id=batch.id, actor=agent)
    setattr(order.items[0], missing_field, None)
    buyer.role = UserRole.OWNER_ADMIN
    with pytest.raises(ValueError, match="confirmed quantities are required"):
        PackingPlanningService(db_session).design_order(order_id=order.id, actor=buyer)


def test_confirmed_batch_cannot_be_rebuilt(db_session):
    buyer, _, order, batch, _ = setup_procurement_batch(db_session)
    batch.status = OrderStatus.PROCUREMENT_CONFIRMED
    order.status = OrderStatus.PROCUREMENT_CONFIRMED
    db_session.commit()

    import pytest

    with pytest.raises(ValueError, match="frozen"):
        PurchasingBatchService(db_session).rebuild_batch(batch.id, actor=buyer)
    db_session.rollback()
    assert db_session.query(PurchasingItem).filter_by(purchasing_list_id=batch.id).count() == 1
    assert order.status == OrderStatus.PROCUREMENT_CONFIRMED


def test_buffer_edits_do_not_compound_and_freeze_after_confirm(db_session):
    buyer, _, order, batch, item = setup_procurement_batch(db_session)
    del order
    buyer.role = UserRole.OWNER_ADMIN
    service = PurchasingBatchService(db_session)

    service.set_item_buffer(item.id, 10, actor=buyer, save_as_default=False)
    service.set_item_buffer(item.id, 10, actor=buyer, save_as_default=False)
    assert item.total_required_pack_count == 11  # 10 ordered packs x 1.10, not 12.1 -> 13
    assert float(item.total_required_weight_kg) == 11.0
    assert item.confirmed_purchased_pack_count is None
    assert item.confirmed_purchased_weight_kg is None

    service.set_batch_buffer(batch.id, 0, actor=buyer)
    assert item.total_required_pack_count == 10

    batch.status = OrderStatus.PROCUREMENT_CONFIRMED
    db_session.commit()
    with pytest.raises(ValueError, match="frozen"):
        service.set_batch_buffer(batch.id, 5, actor=buyer)
