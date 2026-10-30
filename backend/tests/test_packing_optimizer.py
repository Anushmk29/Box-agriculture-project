from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProduceType,
    TemperatureZone,
    WeightClass,
)
from dataclasses import replace
from app.modules.packing.optimizer import (
    DeterministicPackingOptimizer,
    PackingDemand,
    PackingOption,
)


def test_non_food_and_unclassified_products_stay_separate_from_food():
    base = demand("base", material=BoxMaterial.CARDBOARD, temperature=TemperatureZone.NORMAL)
    demands = [
        replace(base, product_id="cracker", produce_type=ProduceType.PACKAGED_FOOD),
        replace(base, product_id="balm", produce_type=ProduceType.PERSONAL_CARE),
        replace(base, product_id="unknown-1", produce_type=ProduceType.OTHER),
        replace(base, product_id="unknown-2", produce_type=ProduceType.OTHER),
    ]
    for route in ("TETE", "NGA"):
        plan = DeterministicPackingOptimizer().solve(
            demands=demands, options=[PackingOption("large", BoxMaterial.CARDBOARD, 20, 100000)], route_code=route,
        )
        assert len(plan.boxes) == 4
        assert all(len(box.items) == 1 for box in plan.boxes)
        assert sum(item.pack_count for box in plan.boxes for item in box.items) == 12


def demand(
    product_id: str,
    *,
    material=BoxMaterial.FOAM,
    temperature=TemperatureZone.COLD,
    control_status=ControlStatus.COMBINABLE,
    botanical_family=None,
    unit_volume_cm3=1000.0,
):
    return PackingDemand(
        product_id=product_id,
        pack_size="1kg",
        pack_count=3,
        unit_weight_kg=1.0,
        box_material=material,
        temperature=temperature,
        control_status=control_status,
        produce_type=ProduceType.HERB,
        weight_class=WeightClass.LIGHT,
        botanical_family=botanical_family,
        unit_volume_cm3=unit_volume_cm3,
    )


def test_optimizer_never_mixes_incompatible_material_or_temperature():
    plan = DeterministicPackingOptimizer().solve(
        demands=[
            demand("cold-foam"),
            demand(
                "normal-cardboard",
                material=BoxMaterial.CARDBOARD,
                temperature=TemperatureZone.NORMAL,
            ),
        ],
        options=[
            PackingOption("foam-5", BoxMaterial.FOAM, 5.0, 5000.0),
            PackingOption("card-5", BoxMaterial.CARDBOARD, 5.0, 5000.0),
        ],
        route_code="TETE",
    )

    assert len(plan.boxes) == 2
    for box in plan.boxes:
        assert len({item.box_material for item in box.items}) == 1
        assert len({item.temperature for item in box.items}) == 1


def test_optimizer_splits_packs_without_exceeding_box_capacity():
    heavy = demand("heavy")
    heavy = PackingDemand(**{**heavy.__dict__, "pack_count": 6, "unit_weight_kg": 2.0})

    plan = DeterministicPackingOptimizer().solve(
        demands=[heavy],
        options=[PackingOption("foam-5", BoxMaterial.FOAM, 5.0, 5000.0)],
        route_code="TETE",
    )

    assert sum(item.pack_count for box in plan.boxes for item in box.items) == 6
    assert all(box.total_net_weight_kg <= 5.0 for box in plan.boxes)
    assert [box.total_net_weight_kg for box in plan.boxes] == [4.0, 4.0, 4.0]


def test_optimizer_segregates_el_items_from_different_botanical_families():
    plan = DeterministicPackingOptimizer().solve(
        demands=[
            demand("chilli", control_status=ControlStatus.EL, botanical_family="Solanaceae"),
            demand("basil", control_status=ControlStatus.EL, botanical_family="Lamiaceae"),
        ],
        options=[PackingOption("foam-10", BoxMaterial.FOAM, 10.0, 10000.0)],
        route_code="TETE",
    )

    assert len(plan.boxes) == 2


def test_optimizer_enforces_92_percent_usable_box_volume():
    plan = DeterministicPackingOptimizer().solve(
        demands=[demand("bulky", unit_volume_cm3=500.0)],
        options=[PackingOption("foam-5", BoxMaterial.FOAM, 5.0, 1000.0)],
        route_code="TETE",
    )

    assert len(plan.boxes) == 3
    assert all(box.total_volume_cm3 <= 920.0 for box in plan.boxes)


def test_optimizer_staff_non_el_mode_permits_el_mixing_with_combinable():
    plan = DeterministicPackingOptimizer().solve(
        demands=[
            demand("celery-el", control_status=ControlStatus.EL, botanical_family="Apiaceae"),
            demand("pennywort-comb", control_status=ControlStatus.COMBINABLE),
        ],
        options=[PackingOption("foam-giant", BoxMaterial.FOAM, 15.0, 70000.0)],
        route_code="NGA",
        packing_mode="NON_EL",
    )

    # In Norway route NGA, EL and COMBINABLE items can share a single box
    assert len(plan.boxes) == 1
    box = plan.boxes[0]
    assert len(box.items) == 2
    item_controls = {it.control_status for it in box.items}
    assert item_controls == {ControlStatus.EL, ControlStatus.COMBINABLE}
