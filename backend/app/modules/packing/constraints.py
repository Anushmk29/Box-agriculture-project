"""The optimizer's constraints applied to persisted floor packing contents."""

import math
from decimal import Decimal

from sqlalchemy.orm import object_session

from app.modules.packing.materials import require_material
from app.modules.packing.optimizer import PackingDemand
from app.modules.packing.profiles import box_volume, product_limit, require_known_el, unit_volume


def validate_contents(box, contents=None, *, catalogue=None, dimensions=None):
    """Contents are (product, pack size, count, net kg) tuples; validate before writes."""
    catalogue = catalogue or box.override_box_type or box.box_type
    rows = contents if contents is not None else [
        (item.product, item.pack_size, item.final_packed_count, float(item.final_net_weight_kg))
        for item in box.items
    ]
    dimensions = dimensions if dimensions is not None else (
        box.custom_length_cm, box.custom_width_cm, box.custom_height_cm
    )
    if any(value is not None for value in dimensions):
        if any(value is None or not math.isfinite(float(value)) or float(value) <= 0 for value in dimensions):
            raise ValueError("Custom dimensions must be complete, finite and positive")
        raise ValueError("Custom dimensions are not supported; select a box size from the dimension workbook")
    volume_capacity = box_volume(catalogue)
    capacity = float(catalogue.max_safe_net_weight_kg)
    if not math.isfinite(capacity) or capacity <= 0 or not math.isfinite(volume_capacity) or volume_capacity <= 0:
        raise ValueError("Box capacity and volume must be finite and positive")
    from app.modules.packing.history import fit_evidence, history_basis
    requires_history = any(history_basis(product) for product, *_ in rows)
    if requires_history or (box.packing_snapshot or {}).get("history_evidence"):
        if fit_evidence(object_session(box), box, rows, catalogue) is not None:
            return
        if requires_history:
            raise ValueError("No exact reviewed historical template covers these contents and box; review guidance before changing packing")
    keys, weight, volume = set(), 0.0, 0.0
    for product, pack_size, count, net in rows:
        if not product or count <= 0 or not math.isfinite(net) or net <= 0:
            raise ValueError("Packed contents require a product, positive count and finite positive weight")
        require_known_el(product, box.packing_mode or "EL")
        packed_volume = unit_volume(product, pack_size, net / count)
        if not math.isfinite(packed_volume) or packed_volume <= 0:
            raise ValueError("Product unit volume must be finite and positive")
        demand = PackingDemand(product.id, pack_size, count, net / count,
            catalogue.box_type, product.temperature, product.control_status,
            product.produce_type, product.weight_class, product.botanical_family, packed_volume)
        keys.add(demand.compatibility_key(box.route_code, box.packing_mode or "EL"))
        require_material(product, catalogue.box_type, box.order)
        weight += net
        volume += count * packed_volume
    if len(keys) > 1:
        raise ValueError("Box contents have incompatible temperature, control, produce type or weight class")
    if len({product.id for product, *_ in rows}) == 1:
        limit = product_limit(rows[0][0], catalogue.id)
        if limit is not None and weight > limit + 0.01:
            raise ValueError("Packed items exceed the reviewed single-product limit for this box")
    if weight > capacity + 0.01:
        raise ValueError("Packed items exceed the box safe capacity")
    if volume > volume_capacity * 0.92:
        raise ValueError("Packed items exceed usable box volume")


def validate_verified_net(box):
    if not box.items:
        raise ValueError(f"Box {box.box_number} has no packed contents")
    item_net = sum(Decimal(str(item.final_net_weight_kg)) for item in box.items)
    physical_net = Decimal(str(box.actual_net_weight_kg))
    if abs(item_net - physical_net) > Decimal("0.01"):
        raise ValueError(
            f"Box {box.box_number}: packed item net weights total {item_net:.2f} kg, "
            f"but verified net is {physical_net:.2f} kg. Reconcile item weights before finalizing."
        )
