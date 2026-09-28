from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AgentHandoff
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
from app.models.packing import Box, BoxItem
from app.models.user import User, UserRole
from app.modules.handoffs.service import HandoffService
from app.modules.packing.service import FinalPackingService, PackingPlanningService
from app.services.packing_override_service import PackingOverrideService


def setup_order(db_session):
    agent = User(
        email="agent2@example.com",
        password_hash="unused",
        full_name="Agent Two",
        role=UserRole.AI_AGENT_2,
        is_service_principal=True,
    )
    packer = User(
        email="packer@example.com",
        password_hash="unused",
        full_name="Floor Packer",
        role=UserRole.GENERAL,
    )
    manager = User(
        email="manager@example.com",
        password_hash="unused",
        full_name="Operations Manager",
        role=UserRole.GENERAL,
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
        botanical_family="Lamiaceae",
        default_pack_sizes=["1kg"],
    )
    catalogue = BoxCatalogue(
        box_type=BoxMaterial.FOAM,
        size_name="Small",
        length_cm=40,
        width_cm=30,
        height_cm=20,
        rated_weight_kg=5,
        volume_m3=0.024,
        max_safe_net_weight_kg=5,
    )
    db_session.add_all([agent, packer, manager, customer, product, catalogue])
    db_session.flush()
    raw_file = RawOrderFile(
        file_name="order.xlsx",
        storage_url="order.xlsx",
        shipment_target_date="2026-09-10",
        route_line="TETE",
        uploaded_by=agent.id,
        status=OrderStatus.HANDOFF_01_SENT,
    )
    db_session.add(raw_file)
    db_session.flush()
    order = ClientOrder(
        raw_order_file_id=raw_file.id,
        customer_id=customer.id,
        order_ref_id="ORD-20260910-BUYER1",
        total_ordered_items_count=6,
        total_estimated_net_weight_kg=6,
        status=OrderStatus.HANDOFF_01_SENT,
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        ClientOrderItem(
            client_order_id=order.id,
            product_id=product.id,
            raw_produce_text="T001 - Herb",
            pack_size="1kg",
            ordered_pack_count=6,
            estimated_net_weight_kg=6,
            confirmed_pack_count=4,
            confirmed_net_weight_kg=4,
        )
    )
    db_session.commit()
    return agent, packer, manager, order


def test_design_persists_deterministic_boxes_and_starts_floor_work(db_session):
    agent, packer, _, order = setup_order(db_session)

    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)

    assert len(boxes) == 1
    assert [sum(item.final_packed_count for item in box.items) for box in boxes] == [4]
    assert order.status == OrderStatus.FLOOR_PACKING
    assert all(box.qr_code_uid.startswith("BOX-TETE-20260910-") for box in boxes)
    assert all(box.qr_image_url == f"http://localhost:3000/scan/{box.qr_code_uid}" for box in boxes)


def test_final_pack_lock_is_immutable_and_produces_handoff_02(db_session):
    agent, packer, _, order = setup_order(db_session)
    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)
    for box in boxes:
        box.actual_net_weight_kg = sum(float(item.final_net_weight_kg) for item in box.items)
        box.actual_gross_weight_kg = float(box.actual_net_weight_kg) + 0.5
    db_session.commit()

    FinalPackingService(db_session).lock_order(order_id=order.id, actor=packer)
    handoff = HandoffService(db_session).return_handoff_02(order_id=order.id, actor=agent)

    assert all(box.is_locked for box in db_session.query(Box).all())
    assert order.status == OrderStatus.HANDOFF_02_RETURNED
    assert handoff.handoff_type == "HANDOFF_02_FINAL_PACKING"
    assert handoff.payload_json["total_boxes_count"] == 1
    assert db_session.query(AgentHandoff).count() == 1


def test_floor_staff_can_record_verified_box_weights_through_api(db_session):
    agent, packer, _, order = setup_order(db_session)
    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)
    box = boxes[0]
    expected_net = round(sum(float(item.final_net_weight_kg) for item in box.items), 2)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    token = create_access_token({"sub": packer.id, "role": packer.role.value})
    response = client.patch(
        f"/api/v3/packing/boxes/{box.id}/weights",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "actual_net_weight_kg": expected_net,
            "actual_gross_weight_kg": expected_net + 0.5,
            "capture_source": "MANUAL",
        },
    )

    assert response.status_code == 200
    assert response.json()["ready_for_lock"] is True
    assert response.json()["actual_net_weight_kg"] == expected_net
    scan = client.get(
        f"/api/v3/packing/scan/{box.qr_code_uid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert scan.status_code == 200
    assert scan.json()["box_id"] == box.id
    assert scan.json()["order_ref_id"] == order.order_ref_id
    app.dependency_overrides.clear()


def test_box_transfer_rejects_cross_order_and_capacity_overflow(db_session):
    agent, packer, _, order = setup_order(db_session)
    designed = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)
    PackingPlanningService(db_session).start_floor_packing(order_id=order.id, actor=packer)
    source = designed[0]
    catalogue = source.box_type
    other_customer = Customer(
        client_code="BUYER-2",
        company_name="Buyer Two",
        billing_email="buyer2@example.com",
        route_group="TETE",
        destination_country="France",
    )
    db_session.add(other_customer)
    db_session.flush()
    other_order = ClientOrder(
        raw_order_file_id=order.raw_order_file_id,
        customer_id=other_customer.id,
        order_ref_id="ORD-20260910-BUYER2",
        status=OrderStatus.FLOOR_PACKING,
    )
    db_session.add(other_order)
    db_session.flush()
    cross_order_target = Box(
        client_order_id=other_order.id,
        box_catalogue_id=catalogue.id,
        box_number=1,
        shipment_date="2026-09-10",
        route_code="TETE",
        qr_code_uid="CROSS-ORDER-TARGET",
    )
    same_order_target = Box(
        client_order_id=order.id,
        box_catalogue_id=catalogue.id,
        box_number=2,
        shipment_date="2026-09-10",
        route_code="TETE",
        qr_code_uid="CAPACITY-TARGET",
    )
    db_session.add_all([cross_order_target, same_order_target])
    db_session.flush()
    db_session.add(
        BoxItem(
            box_id=same_order_target.id,
            product_id=source.items[0].product_id,
            pack_size="1kg",
            final_packed_count=2,
            final_net_weight_kg=2,
        )
    )
    db_session.commit()

    service = PackingOverrideService(db_session)
    try:
        service.transfer_box_contents(source.id, cross_order_target.id, actor=packer)
    except ValueError as exc:
        assert "same client order" in str(exc)
    else:
        raise AssertionError("Cross-order box transfer must be rejected")

    try:
        service.transfer_box_contents(source.id, same_order_target.id, actor=packer)
    except ValueError as exc:
        assert "capacity" in str(exc)
    else:
        raise AssertionError("Over-capacity box transfer must be rejected")


def test_packing_design_uses_sub_customer_route_group(db_session):
    agent, _, _, order = setup_order(db_session)
    order.customer.route_group = "NGA"
    db_session.commit()

    boxes = PackingPlanningService(db_session).design_order(order_id=order.id, actor=agent)

    assert boxes[0].route_code == "NGA"
    assert boxes[0].qr_code_uid.startswith("BOX-NGA-")
