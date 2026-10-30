"""Box identity and labelling rules shared by planned, floor and fast-track packing."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.work_limits import WorkLimit
from app.models.master_data import Customer
from app.models.order import ClientOrder, OrderStatus, RawOrderFile
from app.models.packing import Box


def order_route_code(order: ClientOrder) -> str:
    if order.customer and order.customer.route_group:
        return order.customer.route_group
    return order.raw_file.route_line if order.raw_file else "GENERAL"


def volumetric_weight_kg(length_cm: float, width_cm: float, height_cm: float) -> float:
    """IATA air freight volumetric weight: L x W x H (cm) / 6000."""
    return round((length_cm * width_cm * height_cm) / 6000.0, 2)


def new_box(
    order: ClientOrder, catalogue_id: str, box_number: int, *, route_code: str | None = None
) -> Box:
    """Unsaved Box with its QR identity; caller adds it to the session."""
    route = route_code or order_route_code(order)
    ship_date = order.raw_file.shipment_target_date
    box_id = str(uuid.uuid4())
    qr = f"BOX-{route}-{ship_date.replace('-', '')}-{box_number:03d}-{box_id[:8]}"
    return Box(
        id=box_id,
        order=order,
        client_order_id=order.id,
        box_catalogue_id=catalogue_id,
        box_number=box_number,
        shipment_date=ship_date,
        route_code=route,
        qr_code_uid=qr,
        qr_image_url=f"{settings.PUBLIC_APP_URL.rstrip('/')}/scan/{qr}",
        is_locked=False,
    )


def main_code(order: ClientOrder) -> str:
    """Sticker prefix for the parent account; Tonio/Tete ship as 'TT'."""
    if order.main_customer:
        code = order.main_customer.client_code
        return "TT" if ("TONIO" in code or "TETE" in code) else code.split("-")[0]
    if order.raw_file and order.raw_file.route_line:
        return "TT" if order.raw_file.route_line == "TETE" else order.raw_file.route_line
    return "TT"


def sticker_label(order: ClientOrder, box_number: int, total_boxes: int) -> str:
    sub = order.customer.client_code if order.customer else "SUB"
    return f"{main_code(order)}-{sub} / {box_number} of {max(total_boxes, 1)}"


def orders_in_group(db: Session, group_name: str, shipment_date: str | None) -> list[ClientOrder]:
    """Orders whose customer belongs to a route group ('GROUP CT', 'CT', or 'ALL')."""
    group = group_name.strip()
    query = db.query(ClientOrder).join(Customer, ClientOrder.customer_id == Customer.id).filter(
        ClientOrder.status.in_(
            {
                OrderStatus.HANDOFF_01_SENT,
                OrderStatus.PACKING_DESIGNED,
                OrderStatus.FLOOR_PACKING,
                OrderStatus.FINAL_PACK_LOCKED,
            }
        )
    )
    if group.upper() != "ALL":
        query = query.filter(
            Customer.route_group.ilike(f"%{group}%") | Customer.client_code.ilike(f"{group}%")
        )
    if shipment_date:
        query = query.join(RawOrderFile, ClientOrder.raw_order_file_id == RawOrderFile.id).filter(
            RawOrderFile.shipment_target_date == shipment_date
        )
    orders = query.order_by(ClientOrder.id).limit(501).all()
    if len(orders) > 500:
        raise WorkLimit("This group exceeds 500 orders; select a shipment date or a smaller group")
    return orders
