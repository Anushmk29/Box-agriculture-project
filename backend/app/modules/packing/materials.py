"""One material policy for planning and all floor changes."""

from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import object_session

from app.models.master_data import BoxMaterial, TemperatureZone
from app.models.order import ClientOrder, ClientOrderItem, OrderStatus, RawOrderFile

DEFAULT_FOAM_PRODUCTS = {"11-034", "11-035", "11-169", "11-398", "16-035", "16-108", "16-159"}


def material_for_temperature(temperature):
    """The grouping workbook pairs cold with foam and normal with cardboard."""
    return {
        TemperatureZone.COLD: BoxMaterial.FOAM,
        TemperatureZone.NORMAL: BoxMaterial.CARDBOARD,
    }[TemperatureZone(temperature)]


def preferred_material(product):
    return BoxMaterial.CARDBOARD if product.foam_below_kg is not None else product.box_material


def demand_kg(order, product_id):
    """Original confirmed customer demand, across files in this shipment/route."""
    db = object_session(order) if order is not None else None
    if db is None:
        return None
    raw = order.raw_file
    value = (
        db.query(func.sum(ClientOrderItem.estimated_net_weight_kg))
        .join(ClientOrder, ClientOrderItem.client_order_id == ClientOrder.id)
        .join(RawOrderFile, ClientOrder.raw_order_file_id == RawOrderFile.id)
        .filter(
            ClientOrder.customer_id == order.customer_id,
            ClientOrder.main_customer_id == order.main_customer_id,
            RawOrderFile.shipment_target_date == raw.shipment_target_date,
            RawOrderFile.route_line == raw.route_line,
            ~ClientOrder.status.in_({OrderStatus.CANCELLED, OrderStatus.UNAVAILABLE}),
            or_(
                ClientOrderItem.replacement_product_id == product_id,
                (
                    ClientOrderItem.replacement_product_id.is_(None)
                    & (ClientOrderItem.product_id == product_id)
                ),
            ),
        )
        .scalar()
    )
    return Decimal(str(value)) if value is not None else None


def material_policy(product, order):
    cutoff = product.foam_below_kg
    weight = demand_kg(order, product.id) if cutoff is not None else None
    return {
        "foam_below_kg": float(cutoff) if cutoff is not None else None,
        "customer_demand_kg": float(weight) if weight is not None else None,
    }


def require_material(product, material, order):
    if product.foam_below_kg is None:
        if material != product.box_material:
            raise ValueError("Product is incompatible with the box material")
        return
    if material == BoxMaterial.CARDBOARD:
        return
    weight = demand_kg(order, product.id)
    cutoff = Decimal(str(product.foam_below_kg))
    if weight is None or weight <= 0 or weight >= cutoff:
        label = f"{weight:g} kg" if weight is not None else "unverified demand"
        raise ValueError(
            f"{product.t_code}: cardboard required for {label}; foam is allowed only below {cutoff:g} kg per subcustomer shipment"
        )
