from __future__ import annotations

import math
from dataclasses import dataclass, field
from time import monotonic

from app.core import work_limits as limits
from app.core.work_limits import WorkLimit
from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProduceType,
    TemperatureZone,
    WeightClass,
)


@dataclass(frozen=True)
class PackingDemand:
    product_id: str
    pack_size: str
    pack_count: int
    unit_weight_kg: float
    box_material: BoxMaterial
    temperature: TemperatureZone
    control_status: ControlStatus
    produce_type: ProduceType
    weight_class: WeightClass
    botanical_family: str | None
    unit_volume_cm3: float

    firmness: str | None = None
    single_product_limits: dict[str, float] = field(default_factory=dict)

    def compatibility_key(self, route_code: str, packing_mode: str = "EL") -> tuple[str, ...]:
        if packing_mode == "NON_EL":
            control = "NON_EL_MIX_ALLOWED"
        elif self.control_status == ControlStatus.EL:
            # Missing family data is isolated per product rather than mixed unsafely.
            control = f"EL:{self.botanical_family or self.product_id}"
        else:
            control = self.control_status.value
        if self.produce_type == ProduceType.OTHER:
            # Unclassified products must not be mixed just because both are OTHER.
            control += f":REVIEW:{self.product_id}"
        return (
            self.box_material.value,
            self.temperature.value,
            control,
            self.produce_type.value,
            self.weight_class.value,
        )


@dataclass(frozen=True)
class PackingOption:
    id: str
    material: BoxMaterial
    max_safe_net_weight_kg: float
    box_volume_cm3: float

    @property
    def usable_volume_cm3(self) -> float:
        return self.box_volume_cm3 * 0.92


@dataclass
class PlannedItem:
    product_id: str
    pack_size: str
    pack_count: int
    unit_weight_kg: float
    box_material: BoxMaterial
    temperature: TemperatureZone
    control_status: ControlStatus
    produce_type: ProduceType
    weight_class: WeightClass
    unit_volume_cm3: float | None

    @property
    def net_weight_kg(self) -> float:
        return round(self.pack_count * self.unit_weight_kg, 4)


@dataclass
class PlannedBox:
    option: PackingOption
    compatibility_key: tuple[str, ...]
    items: list[PlannedItem] = field(default_factory=list)
    history_evidence: dict | None = None

    @property
    def total_net_weight_kg(self) -> float:
        return round(sum(item.net_weight_kg for item in self.items), 4)

    @property
    def total_volume_cm3(self) -> float | None:
        if any(item.unit_volume_cm3 is None for item in self.items):
            return None
        return round(sum(item.pack_count * item.unit_volume_cm3 for item in self.items), 2)


@dataclass(frozen=True)
class PackingPlan:
    route_code: str
    boxes: list[PlannedBox]


class DeterministicPackingOptimizer:
    """Best-fit-decreasing weight planner with the PRD's hard segregation rules."""

    def solve(
        self,
        *,
        demands: list[PackingDemand],
        options: list[PackingOption],
        route_code: str,
        packing_mode: str = "EL",
    ) -> PackingPlan:
        if packing_mode not in {"EL", "NON_EL"}:
            raise ValueError("Select EL or Non-EL packing rules")
        if len(demands) > limits.MAX_DEMANDS or len(options) > limits.MAX_OPTIONS:
            raise WorkLimit("Packing exceeds 1000 demand rows or 200 box options")
        if any(type(d.pack_count) is not int for d in demands):
            raise ValueError("Packing counts must be whole numbers")
        if sum(d.pack_count for d in demands) > limits.MAX_PACKS:
            raise WorkLimit("Packing exceeds the 250000-pack order budget; split the order")
        self._deadline, self._checks = monotonic() + 2, 0
        plans = [self._solve(demands=demands, options=options, route_code=route_code, packing_mode=packing_mode, largest_first=large) for large in (False, True)]
        return min(plans, key=lambda plan: (len(plan.boxes), sum(box.option.usable_volume_cm3 - box.total_volume_cm3 for box in plan.boxes), tuple(box.option.id for box in plan.boxes)))

    def _solve(self, *, demands, options, route_code, packing_mode, largest_first):
        if not options:
            raise ValueError("At least one active box option is required")
        if any(
            d.pack_count < 1 or not math.isfinite(d.unit_weight_kg) or d.unit_weight_kg <= 0 or not math.isfinite(d.unit_volume_cm3) or d.unit_volume_cm3 <= 0 for d in demands
        ):
            raise ValueError("Packing demand counts, weights, and volumes must be positive")

        if any(not math.isfinite(o.max_safe_net_weight_kg) or o.max_safe_net_weight_kg <= 0 or not math.isfinite(o.box_volume_cm3) or o.box_volume_cm3 <= 0 for o in options):
            raise ValueError("Box weight and volume capacities must be positive and finite")
        usable = sorted(options, key=lambda o: ((-1 if largest_first else 1) * o.usable_volume_cm3, o.max_safe_net_weight_kg, o.id))
        boxes: list[PlannedBox] = []

        units = list(demands)
        units.sort(
            key=lambda item: (
                -max(item.unit_weight_kg / max(o.max_safe_net_weight_kg for o in usable), item.unit_volume_cm3 / max(o.usable_volume_cm3 for o in usable)),
                item.compatibility_key(route_code, packing_mode),
                item.product_id,
                item.pack_size,
            )
        )

        for unit in units:
            remaining = unit.pack_count
            key = unit.compatibility_key(route_code, packing_mode)
            while remaining:
                self._checks += sum(1 + len(box.items) for box in boxes) + len(usable)
                if self._checks > limits.MAX_PACKING_CHECKS or monotonic() > self._deadline:
                    raise WorkLimit("Packing exceeded its work budget; split or simplify the order")
                candidates = [
                    box
                    for box in boxes
                    if box.compatibility_key == key
                    and box.total_net_weight_kg + unit.unit_weight_kg
                    <= box.option.max_safe_net_weight_kg + 1e-9
                    and (any(i.product_id != unit.product_id for i in box.items) or box.total_net_weight_kg + unit.unit_weight_kg <= unit.single_product_limits.get(box.option.id, float("inf")) + 1e-9)
                    and box.total_volume_cm3 + unit.unit_volume_cm3
                    <= box.option.usable_volume_cm3 + 1e-9
                ]
                if candidates:
                    positions = {id(candidate): index for index, candidate in enumerate(boxes)}
                    box = min(
                        candidates,
                        key=lambda candidate: (
                            max((candidate.option.max_safe_net_weight_kg - candidate.total_net_weight_kg - unit.unit_weight_kg) / candidate.option.max_safe_net_weight_kg, (candidate.option.usable_volume_cm3 - candidate.total_volume_cm3 - unit.unit_volume_cm3) / candidate.option.usable_volume_cm3),
                            positions[id(candidate)],
                        ),
                    )
                else:
                    fitting = [
                        option
                        for option in usable
                        if option.material == unit.box_material
                        and option.max_safe_net_weight_kg + 1e-9 >= unit.unit_weight_kg
                        and option.usable_volume_cm3 + 1e-9 >= unit.unit_volume_cm3
                        and unit.single_product_limits.get(option.id, float("inf")) + 1e-9 >= unit.unit_weight_kg
                    ]
                    if not fitting:
                        raise ValueError(
                            f"No {unit.box_material.value} box can safely hold "
                            f"one {unit.pack_size} pack of product {unit.product_id}"
                        )
                    box = PlannedBox(option=fitting[0], compatibility_key=key)
                    if len(boxes) >= limits.MAX_BOXES:
                        raise WorkLimit("Packing exceeds the 2000-box order budget; split the order")
                    boxes.append(box)

                capacity = min(remaining,
                    math.floor((box.option.max_safe_net_weight_kg - box.total_net_weight_kg + 1e-9) / unit.unit_weight_kg),
                    math.floor((box.option.usable_volume_cm3 - box.total_volume_cm3 + 1e-9) / unit.unit_volume_cm3))
                if not any(item.product_id != unit.product_id for item in box.items):
                    pure_limit = unit.single_product_limits.get(box.option.id)
                    if pure_limit is not None:
                        capacity = min(capacity, math.floor((pure_limit - box.total_net_weight_kg + 1e-9) / unit.unit_weight_kg))
                if capacity < 1:
                    raise ValueError("A pack cannot fit within the reviewed box capacities")
                remaining -= capacity
                existing = next(
                    (
                        item
                        for item in box.items
                        if item.product_id == unit.product_id and item.pack_size == unit.pack_size
                    ),
                    None,
                )
                if existing:
                    existing.pack_count += capacity
                else:
                    box.items.append(
                        PlannedItem(
                            product_id=unit.product_id,
                            pack_size=unit.pack_size,
                            pack_count=capacity,
                            unit_weight_kg=unit.unit_weight_kg,
                            box_material=unit.box_material,
                            temperature=unit.temperature,
                            control_status=unit.control_status,
                            produce_type=unit.produce_type,
                            weight_class=unit.weight_class,
                            unit_volume_cm3=unit.unit_volume_cm3,
                        )
                    )

        return PackingPlan(route_code=route_code.upper(), boxes=boxes)
