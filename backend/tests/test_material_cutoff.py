from decimal import Decimal

import pytest
from test_packing_lifecycle import setup_order
from test_simplified_workflow import headers

from app.models.master_data import BoxCatalogue, BoxMaterial
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile
from app.modules.packing.materials import demand_kg, require_material
from app.modules.packing.planning import generate_plan, persist_plan
from app.modules.packing.profiles import stale_reasons

pytest_plugins = ["test_simplified_workflow"]


@pytest.mark.parametrize("weight,foam_allowed", [(0.999, True), (1, False), (1.001, False)])
def test_cutoff_uses_customer_demand_and_exact_boundary(db_session, weight, foam_allowed):
    _, _, _, order = setup_order(db_session)
    product = order.items[0].product
    product.foam_below_kg = 1
    order.items[0].estimated_net_weight_kg = weight
    # Purchase overage never contributes to the policy quantity.
    order.items[0].confirmed_net_weight_kg = 10
    db_session.flush()
    assert demand_kg(order, product.id) == Decimal(str(weight))
    require_material(product, BoxMaterial.CARDBOARD, order)
    if foam_allowed:
        require_material(product, BoxMaterial.FOAM, order)
    else:
        with pytest.raises(ValueError, match="cardboard required"):
            require_material(product, BoxMaterial.FOAM, order)


def test_cutoff_aggregates_pack_sizes_and_separate_files_for_same_shipment(db_session):
    _, _, _, order = setup_order(db_session)
    product = order.items[0].product
    product.foam_below_kg = 1
    order.items[0].estimated_net_weight_kg = Decimal("0.6")
    other_raw = RawOrderFile(
        file_name="extra.xlsx",
        storage_url="extra.xlsx",
        shipment_target_date=order.raw_file.shipment_target_date,
        route_line=order.raw_file.route_line,
        uploaded_by=order.raw_file.uploaded_by,
        status=OrderStatus.SPLIT_AND_VALIDATED,
    )
    db_session.add(other_raw)
    db_session.flush()
    other = ClientOrder(
        raw_order_file_id=other_raw.id,
        customer_id=order.customer_id,
        order_ref_id="extra",
        status=OrderStatus.SPLIT_AND_VALIDATED,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        ClientOrderItem(
            client_order_id=other.id,
            product_id=product.id,
            raw_produce_text="extra garlic",
            pack_size="200g",
            ordered_pack_count=2,
            estimated_net_weight_kg=Decimal("0.4"),
        )
    )
    db_session.flush()
    assert demand_kg(order, product.id) == 1
    with pytest.raises(ValueError, match="cardboard required"):
        require_material(product, BoxMaterial.FOAM, order)
    other_raw.shipment_target_date = "2026-09-11"
    db_session.flush()
    assert demand_kg(order, product.id) == Decimal("0.6")


def test_product_weights_stay_separate(db_session):
    from app.models.master_data import Product

    _, _, _, order = setup_order(db_session)
    garlic = order.items[0].product
    garlic.foam_below_kg = 1
    order.items[0].estimated_net_weight_kg = Decimal("0.6")
    onion = Product(
        t_code="11-398",
        name_en="Onion",
        name_vi="Onion",
        name_th="Onion",
        box_material=garlic.box_material,
        temperature=garlic.temperature,
        control_status=garlic.control_status,
        produce_type=garlic.produce_type,
        weight_class=garlic.weight_class,
        foam_below_kg=1,
    )
    db_session.add(onion)
    db_session.flush()
    db_session.add(
        ClientOrderItem(
            client_order_id=order.id,
            product_id=onion.id,
            raw_produce_text="Onion",
            pack_size="100g",
            ordered_pack_count=6,
            estimated_net_weight_kg=Decimal("0.6"),
        )
    )
    db_session.flush()
    for product in (garlic, onion):
        assert demand_kg(order, product.id) == Decimal("0.6")
        require_material(product, BoxMaterial.FOAM, order)


def test_automatic_planning_prefers_cardboard_and_snapshots_cutoff(db_session):
    _, _, _, order = setup_order(db_session)
    product = order.items[0].product
    product.foam_below_kg = 1
    # External dimensions match an approved cardboard reference entry.
    from app.modules.packing.catalogue import dimension_reference, reference_for

    # Use the same dimensions as the reference catalogue rather than inventing a box.
    entry = next(r for r in dimension_reference()["rows"] if r["box_type"] == "CARDBOARD")
    box = BoxCatalogue(
        box_type=BoxMaterial.CARDBOARD,
        size_name=entry["name_th"],
        length_cm=entry["length_cm"],
        width_cm=entry["width_cm"],
        height_cm=entry["height_cm"],
        rated_weight_kg=20,
        max_safe_net_weight_kg=20,
        volume_m3=0.1,
    )
    db_session.add(box)
    db_session.flush()
    assert reference_for(box)
    plan = generate_plan(db_session, order, "EL")
    assert all(b.option.material == BoxMaterial.CARDBOARD for b in plan.boxes)
    boxes = persist_plan(db_session, order, plan, "EL")
    saved = boxes[0].packing_snapshot
    assert not stale_reasons(boxes[0])
    assert next(iter(saved["items"].values()))["material_policy"]["customer_demand_kg"] == 6
    assert next(iter(saved["items"].values()))["material_policy"]["foam_below_kg"] == 1
    product.foam_below_kg = 2
    db_session.flush()
    assert stale_reasons(boxes[0])
    boxes[0].is_locked = True
    assert not stale_reasons(boxes[0]) and boxes[0].packing_snapshot == saved


def test_cutoff_edit_is_revision_checked_and_persists(db_session, transactional_client):
    _, actor, _, order = setup_order(db_session)
    product = order.items[0].product
    url = "/api/v3/packing-setup/table/bulk"
    revision = product.packing_revision
    body = {
        "edits": [
            {"id": product.id, "expected_revision": revision, "changes": {"foam_below_kg": 1}}
        ]
    }
    result = transactional_client.post(url, headers=headers(actor), json=body)
    assert result.status_code == 200, result.text
    assert result.json()["results"][0]["product"]["foam_below_kg"] == 1
    db_session.expire_all()
    assert float(product.foam_below_kg) == 1
    stale = transactional_client.post(url, headers=headers(actor), json=body)
    assert stale.json()["results"][0].get("error")


def test_splitting_boxes_cannot_bypass_the_customer_cutoff(db_session):
    from app.modules.packing.boxes import new_box
    from app.modules.packing.constraints import validate_contents

    _, _, _, order = setup_order(db_session)
    product = order.items[0].product
    product.foam_below_kg = 1
    order.items[0].estimated_net_weight_kg = Decimal("1.2")
    catalogue = db_session.query(BoxCatalogue).one()
    box = new_box(order, catalogue.id, 1)
    box.box_type = catalogue
    box.packing_mode = "EL"
    db_session.add(box)
    db_session.flush()
    # A half-kilogram allocation is still part of a 1.2 kg customer order.
    with pytest.raises(ValueError, match="cardboard required"):
        validate_contents(box, [(product, "100g", 6, 0.6)])
    order.items[0].estimated_net_weight_kg = Decimal("0.999")
    db_session.flush()
    validate_contents(box, [(product, "100g", 6, 0.6)])
