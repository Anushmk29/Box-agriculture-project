"""Interpret source units once, before procurement or packing sees quantities."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.packs import pack_key
from app.modules.packing.profiles import profile_for

COUNT_UNITS = {"pack", "pc", "box", "bag", "bunch", "tray"}


def unit_key(value: str | None) -> str:
    value = (value or "").strip().casefold().rstrip(".")
    return {
        "kgs": "kg",
        "kilogram": "kg",
        "kilograms": "kg",
        "กก": "kg",
        "gr": "g",
        "gms": "g",
        "gram": "g",
        "grams": "g",
        "packs": "pack",
        "pkt": "pack",
        "pkts": "pack",
        "packet": "pack",
        "packets": "pack",
        "pcs": "pc",
        "piece": "pc",
        "pieces": "pc",
        "boxes": "box",
        "bags": "bag",
        "bunches": "bunch",
        "trays": "tray",
        "carton": "box",
        "cartons": "box",
    }.get(value, value)


def decimal_value(value) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Quantity must be a number") from None
    if not number.is_finite():
        raise ValueError("Quantity must be finite")
    return number


def source_amount(raw, default_unit="unknown"):
    value = str(raw).strip()
    match = re.fullmatch(r"([+-]?\d+(?:[.,]\d+)?)\s*([A-Za-z.]+)?", value)
    if not match:
        raise ValueError(f"Order amount '{value}' is not a number with a supported unit")
    amount = decimal_value(match[1])
    if amount < 0:
        raise ValueError("Negative order quantities need an explicit cancellation")
    return float(amount), unit_key(match[2] or default_unit)


def normalize_pack(raw, *, numeric_unit=None, adjacent_kg=None):
    """Bare numbers only gain units through an explicit source-template contract."""
    label = str(raw).strip() if raw is not None else ""
    if re.fullmatch(r"\d+(?:[.,]\d+)?", label):
        if numeric_unit not in {"g", "kg"}:
            return label, ["Numeric Packing needs a confirmed grams or kilograms unit"]
        label += numeric_unit
    if not label:
        return None, []
    # kg alone describes loose ordering, not a physical one-kilogram bag.
    if unit_key(label) in {"kg", "g"}:
        return "loose", []
    label = pack_key(label)
    nominal = label_weight(label)
    if adjacent_kg is not None and nominal is not None:
        if abs(nominal - decimal_value(adjacent_kg)) > Decimal("0.00005"):
            return label, [f"Packing {label} conflicts with adjacent pack weight {adjacent_kg} kg"]
    return label, []


def label_weight(label: str | None) -> Decimal | None:
    """Exact nominal weight; ranges, approximate sizes and compound packs need review."""
    label = pack_key(label) if label else ""
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)g(?:\s*(?:/?\s*(?:bag|tray|box|carton|pack|bunch|pkt)))?", label
    )
    if not match:
        return None
    return decimal_value(match[1]) / 1000


@dataclass(frozen=True)
class OrderQuantity:
    pack_size: str
    packs: int
    net_kg: Decimal
    weight_source: str


def quantity_snapshot(item):
    """Bind an unchanged review to the values staff actually saw."""
    return {"quantity": item.quantity, "unit": item.unit, "pack_size": item.pack_size,
            "net_weight_kg": item.net_weight_kg, "quantity_issues": list(item.quantity_issues),
            "requires_packing_review": bool(item.source.get("requires_packing_review"))}


def kept_as_ordered(item):
    review = item.source.get("quantity_review") or {}
    return (review.get("decision") == "KEEP_AS_ORDERED" and bool(review.get("reviewed_by"))
            and review.get("values") == quantity_snapshot(item))


def weight_only_line(item):
    """Zero requested packs means unknown, only for explicitly reviewed weight orders."""
    lines = (item.quantity_source or {}).get("lines", [])
    return (item.ordered_pack_count == 0 and item.estimated_net_weight_kg > 0 and bool(lines)
            and all(line.get("weight_source") == "REVIEWED_ORDER_WEIGHT" for line in lines))


def derive_quantity(item, product=None) -> OrderQuantity:
    if item.quantity_issues:
        raise ValueError("; ".join(item.quantity_issues))
    reviewed = kept_as_ordered(item)
    if item.source.get("requires_packing_review") and not item.source.get("reviewed_by") and not reviewed:
        raise ValueError("Packing details required: staff must complete and confirm this row before Purchasing")
    amount = decimal_value(item.quantity)
    if amount <= 0:
        raise ValueError("Order amount must be positive")
    unit = unit_key(item.unit)
    size = item.pack_size or "std"
    try:
        nominal = label_weight(size)
    except ValueError:
        raise ValueError("Packing needs an explicit unit, for example 100g") from None
    source = "ORDER_LABEL" if nominal else "REVIEWED_PROFILE"
    profile = profile_for(product, size, strict=False) if product and not (
        reviewed and unit in {"kg", "g"} and nominal is None
    ) else None
    if nominal is None and profile and profile.reviewed and profile.net_weight_kg is not None:
        nominal = decimal_value(profile.net_weight_kg)
    declared = decimal_value(item.net_weight_kg) if item.net_weight_kg is not None else None
    if declared is not None and declared <= 0:
        raise ValueError("Declared net weight must be positive")
    if unit in {"kg", "g"}:
        net = amount / 1000 if unit == "g" else amount
        if declared is not None and abs(declared - net) > Decimal("0.0001"):
            raise ValueError("Declared net weight conflicts with the order amount and unit")
        if nominal is None or nominal <= 0:
            if reviewed and nominal is None and Decimal("0.0001") <= net <= Decimal("99999999"):
                # Preserve the customer's kg and packing label. The buyer supplies
                # actual counts; never manufacture a nominal pack size or count.
                return OrderQuantity(pack_key(size), 0, net, "REVIEWED_ORDER_WEIGHT")
            raise ValueError(
                "Enter the physical pack size or a reviewed piece/bunch weight for this weight order"
            )
        count = net / nominal
    elif unit in COUNT_UNITS:
        count = amount
        if nominal is None and declared is None:
            raise ValueError(
                "Enter a nominal pack weight, declared line net kg, or a reviewed piece/bunch profile"
            )
        net = declared if declared is not None else count * nominal
        if (
            declared is not None
            and nominal is not None
            and abs(declared - count * nominal) > Decimal("0.005")
        ):
            raise ValueError("Declared net weight conflicts with pack count × nominal pack weight")
    else:
        raise ValueError(
            "Select the order quantity unit: kg, g, packs, pieces, boxes, bags, bunches or trays"
        )
    if abs(count - count.to_integral_value()) > Decimal("0.000001"):
        if reviewed and unit in {"kg", "g"} and Decimal("0.0001") <= net <= Decimal("99999999"):
            # A weight order may not divide into whole nominal packs. Keep its
            # weight and packing label; purchasing records the actual count.
            return OrderQuantity(pack_key(size), 0, net, "REVIEWED_ORDER_WEIGHT")
        raise ValueError(
            f"{count.normalize()} packs is not a whole number; correct the amount or pack size"
        )
    packs = int(count.to_integral_value())
    if packs <= 0 or packs > 2_000_000_000 or not Decimal("0.0001") <= net <= Decimal("99999999"):
        raise ValueError("Order quantity exceeds the supported range")
    return OrderQuantity(
        pack_key(size) if item.pack_size else size,
        packs,
        net,
        "DECLARED_LINE" if declared is not None else source,
    )
