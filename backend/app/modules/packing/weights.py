"""Box totals derived from saved packing contents."""

from decimal import Decimal

PACKAGING_ALLOWANCE_KG = Decimal("0.50")


def derive_box_weights(box) -> None:
    net = sum((Decimal(str(item.final_net_weight_kg)) for item in box.items), Decimal(0))
    box.actual_net_weight_kg = net.quantize(Decimal("0.01"))
    box.actual_gross_weight_kg = box.actual_net_weight_kg + PACKAGING_ALLOWANCE_KG
