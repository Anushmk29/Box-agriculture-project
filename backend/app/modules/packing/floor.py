"""Floor packing: physical adjustments to a designed packing plan, plus template fast-track.

Every change needs an actor, is audited, is only allowed while the order is
PACKING_DESIGNED or FLOOR_PACKING, and clears cached box totals when contents change.
Weights are always derived from packed items, never taken from the caller.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.core.policy import Action, require
from app.core.values import INT32_MAX, PACKED_WEIGHT_MAX, WEIGHT_STEP, fixed_decimal, required_text
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.models.order import ClientOrder, OrderStatus
from app.models.packing import AdjustmentType, Box, BoxItem, TransportationManifest
from app.models.user import User
from app.modules.handoffs.service import HandoffService
from app.modules.orders.intake import pack_weight_kg
from app.modules.packing.boxes import new_box, orders_in_group, volumetric_weight_kg
from app.modules.packing.catalogue import reference_for
from app.modules.packing.constraints import validate_contents
from app.modules.packing.profiles import (
    PACKING_MODES,
    box_volume,
    require_current,
    snapshot_for,
)
from app.modules.packing.weights import derive_box_weights
from app.modules.workflow.service import WorkflowService

EDITABLE_STATUSES = frozenset({OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING})


def _require_editable(order: ClientOrder) -> None:
    if order.status not in EDITABLE_STATUSES:
        raise ValueError(
            f"Packing for {order.order_ref_id} is {order.status.value} and cannot be changed"
        )


def _clear_weights(*boxes: Box) -> None:
    for box in boxes:
        box.actual_net_weight_kg = None
        box.actual_gross_weight_kg = None
        if settings.PACKING_PROFILES_ENABLED:
            db = object_session(box)
            db.flush()
            db.expire(box, ["items"])
            validate_contents(box)
            box.packing_snapshot = snapshot_for(box)


class FloorPackingService:
    def __init__(self, db: Session):
        self.db = db

    def _audit(self, actor: User, event: str, box: Box, before: dict, after: dict) -> None:
        # All boxes in an order share one revision, including internal edits.
        box.order.packing_revision += 1
        self.db.add(
            AuditLog(
                event_type=event,
                actor_id=actor.id,
                actor_role=actor.role.value,
                target_table="boxes",
                target_id=box.id,
                before_state=before,
                after_state=after,
            )
        )

    def _editable_box(self, box_id: str) -> Box:
        box = self.db.query(Box).filter(Box.id == box_id).first()
        if not box:
            raise ValueError(f"Box {box_id} not found")
        self.db.query(ClientOrder).filter_by(id=box.client_order_id).with_for_update().populate_existing().one()
        self.db.query(Box).filter_by(id=box.id).with_for_update().populate_existing().one()
        if box.is_locked:
            raise ValueError(f"Box {box_id} is locked and immutable")
        _require_editable(box.order)
        require_current(box)
        return box

    def save_item(self, box_id: str, *, actor: User, product_id: str, pack_size: str,
                  quantity: int, net_weight_kg: float, item_id: str | None = None) -> BoxItem | None:
        require(actor, Action.FLOOR_PACKING)
        net_weight_kg = float(fixed_decimal(net_weight_kg, quantum=WEIGHT_STEP, maximum=PACKED_WEIGHT_MAX))
        pack_size = required_text(pack_size, limit=64)
        if type(quantity) is not int or not 0 <= quantity <= INT32_MAX:
            raise ValueError("Packed quantity must be a supported nonnegative integer")
        box = self._editable_box(box_id)
        product = self.db.query(Product).filter_by(id=product_id, is_active=True).first()
        if not product:
            raise ValueError("Select an active master product")
        if not pack_size.strip() or quantity < 0 or net_weight_kg < 0:
            raise ValueError("Pack size, quantity and weight must be valid")
        item = next((line for line in box.items if line.id == item_id), None) if item_id else None
        if item_id and item is None:
            raise ValueError("Packing item not found in this box")
        if quantity == 0:
            if item is None:
                raise ValueError("Cannot add an empty packing item")
            before = {"product_id": item.product_id, "quantity": item.final_packed_count, "net_weight_kg": float(item.final_net_weight_kg)}
            self.db.delete(item)
            self._audit(actor, "BOX_ITEM_REMOVED", box, before, {})
            _clear_weights(box)
            self.db.flush()
            return None
        if net_weight_kg <= 0:
            raise ValueError("A positive net weight is required for packed items")
        others = [line for line in box.items if line is not item]
        if any(line.product_id == product.id and line.pack_size == pack_size.strip() for line in others):
            raise ValueError("This SKU and pack size already exist; edit that line instead")
        contents = [(line.product, line.pack_size, line.final_packed_count, float(line.final_net_weight_kg)) for line in others]
        validate_contents(box, [*contents, (product, pack_size, quantity, net_weight_kg)])
        before = {"product_id": item.product_id, "quantity": item.final_packed_count, "net_weight_kg": float(item.final_net_weight_kg)} if item else {}
        if item is None:
            item = BoxItem(box_id=box.id)
            self.db.add(item)
        if before.get("product_id", product.id) != product.id:
            item.adjustment_type = AdjustmentType.SUBSTITUTED
        elif quantity < before.get("quantity", 0):
            item.adjustment_type = AdjustmentType.SHORTAGE_REDUCED
        else:
            item.adjustment_type = AdjustmentType.QUANTITY_INCREASED
        item.product_id = product.id
        item.pack_size = pack_size.strip()
        item.final_packed_count = quantity
        item.final_net_weight_kg = round(net_weight_kg, 4)
        item.adjustment_notes = "Floor adjustment; totals calculated at finalization"
        _clear_weights(box)
        self._audit(actor, "BOX_ITEM_UPDATED", box, before, {"product_id": product.id, "quantity": quantity, "net_weight_kg": item.final_net_weight_kg})
        self.db.flush()
        return item

    def _order_boxes(self, order_id: str) -> list[Box]:
        return (
            self.db.query(Box).filter(Box.client_order_id == order_id).order_by(Box.box_number).all()
        )

    def override_box_size(
        self,
        box_id: str,
        *,
        actor: User,
        new_catalogue_id: str | None = None,
        custom_length_cm: float | None = None,
        custom_width_cm: float | None = None,
        custom_height_cm: float | None = None,
        override_reason: str | None = None,
    ) -> Box:
        """Match the plan to the physical box used; totals are recalculated at finalization."""
        box = self._editable_box(box_id)
        before = {"box_catalogue_id": box.override_box_catalogue_id or box.box_catalogue_id}
        catalogue = box.override_box_type or box.box_type
        if any(value is not None for value in (custom_length_cm, custom_width_cm, custom_height_cm)):
            raise ValueError("Custom dimensions are not supported; select a box size from the dimension workbook")
        if new_catalogue_id:
            catalogue = self.db.query(BoxCatalogue).filter_by(id=new_catalogue_id, is_active=True).first()
            if not catalogue:
                raise ValueError(f"Active BoxCatalogue {new_catalogue_id} not found")
        dimensions = (None, None, None) if new_catalogue_id else (
            (box.custom_length_cm, box.custom_width_cm, box.custom_height_cm))
        validate_contents(box, catalogue=catalogue, dimensions=dimensions)
        dims = (
            float(catalogue.length_cm), float(catalogue.width_cm), float(catalogue.height_cm))
        if new_catalogue_id:
            box.override_box_catalogue_id = catalogue.id
            box.custom_length_cm = box.custom_width_cm = box.custom_height_cm = None
        if dims:
            box.volumetric_weight_kg = volumetric_weight_kg(*dims)
            box.chargeable_weight_kg = box.volumetric_weight_kg
        box.is_manually_overridden = True
        if override_reason:
            box.override_reason = override_reason
        _clear_weights(box)
        self._audit(
            actor,
            "BOX_SIZE_OVERRIDDEN",
            box,
            before,
            {
                "box_catalogue_id": box.override_box_catalogue_id or box.box_catalogue_id,
                "dims": list(dims) if dims else None,
            },
        )
        self.db.flush()
        self.db.refresh(box)
        return box

    def add_extra_box_to_order(
        self,
        client_order_id: str,
        box_catalogue_id: str,
        *,
        actor: User,
        is_mixed_box: bool = True,
        packing_mode: str | None = None,
    ) -> Box:
        order = self.db.query(ClientOrder).filter(ClientOrder.id == client_order_id).with_for_update().populate_existing().first()
        if not order:
            raise ValueError(f"ClientOrder {client_order_id} not found")
        _require_editable(order)
        catalogue = self.db.query(BoxCatalogue).filter(BoxCatalogue.id == box_catalogue_id).first()
        if not catalogue or not catalogue.is_active:
            raise ValueError(f"Active BoxCatalogue {box_catalogue_id} not found")
        box_volume(catalogue)
        if settings.PACKING_PROFILES_ENABLED and packing_mode not in PACKING_MODES:
            raise ValueError("Select EL or Non-EL packing rules for the new box")

        next_number = max((b.box_number for b in self._order_boxes(order.id)), default=0) + 1
        box = new_box(order, catalogue.id, next_number)
        box.packing_mode = packing_mode or "EL"
        box.box_type = catalogue
        box.packing_snapshot = snapshot_for(box, [])
        box.is_mixed_box = is_mixed_box
        box.volumetric_weight_kg = volumetric_weight_kg(
            float(catalogue.length_cm), float(catalogue.width_cm), float(catalogue.height_cm)
        )
        box.chargeable_weight_kg = box.volumetric_weight_kg
        self.db.add(box)
        self.db.flush()
        self._audit(
            actor, "BOX_ADDED", box, {}, {"box_number": next_number, "box_catalogue_id": catalogue.id}
        )
        self.db.flush()
        self.db.refresh(box)
        return box

    def move_items_between_boxes(
        self,
        source_box_id: str,
        target_box_id: str,
        product_id: str,
        pack_size: str,
        quantity_to_move: int,
        *,
        actor: User,
    ) -> dict[str, Any]:
        """Move packs within one order; moved weight is the source line's per-pack weight."""
        if source_box_id == target_box_id:
            raise ValueError("Source and target boxes must be different")
        source_box = self._editable_box(source_box_id)
        target_box = self._editable_box(target_box_id)
        if source_box.client_order_id != target_box.client_order_id:
            raise ValueError("Box moves must stay within the same client order")

        def line(box: Box) -> BoxItem | None:
            return next(
                (i for i in box.items if i.product_id == product_id and i.pack_size == pack_size),
                None,
            )

        source_item = line(source_box)
        if not source_item or source_item.final_packed_count < quantity_to_move:
            raise ValueError("Insufficient quantity in source box to move")
        moved_kg = round(
            float(source_item.final_net_weight_kg)
            * quantity_to_move
            / source_item.final_packed_count,
            4,
        )
        capacity = float(
            (target_box.override_box_type or target_box.box_type).max_safe_net_weight_kg
        )
        if sum(float(i.final_net_weight_kg) for i in target_box.items) + moved_kg > capacity + 0.01:
            raise ValueError("Move would exceed the target box safe capacity")

        contents = [(i.product, i.pack_size, i.final_packed_count, float(i.final_net_weight_kg)) for i in target_box.items]
        validate_contents(target_box, [*contents, (source_item.product, pack_size, quantity_to_move, moved_kg)])
        source_item.final_packed_count -= quantity_to_move
        source_item.final_net_weight_kg = max(
            0.0, round(float(source_item.final_net_weight_kg) - moved_kg, 4)
        )
        source_item.adjustment_type = AdjustmentType.SHORTAGE_REDUCED
        source_item.adjustment_notes = (
            f"Moved {quantity_to_move} packs to Box #{target_box.box_number}"
        )
        if source_item.final_packed_count == 0:
            self.db.delete(source_item)

        target_item = line(target_box)
        if target_item:
            target_item.final_packed_count += quantity_to_move
            target_item.final_net_weight_kg = round(
                float(target_item.final_net_weight_kg) + moved_kg, 4
            )
        else:
            target_item = BoxItem(
                box_id=target_box.id,
                product_id=product_id,
                pack_size=pack_size,
                final_packed_count=quantity_to_move,
                final_net_weight_kg=moved_kg,
            )
            self.db.add(target_item)
        target_item.adjustment_type = AdjustmentType.QUANTITY_INCREASED
        target_item.adjustment_notes = (
            f"Received {quantity_to_move} packs from Box #{source_box.box_number}"
        )

        _clear_weights(source_box, target_box)
        self._audit(
            actor,
            "BOX_ITEMS_MOVED",
            target_box,
            {"source_box_id": source_box.id},
            {
                "product_id": product_id,
                "pack_size": pack_size,
                "packs": quantity_to_move,
                "net_kg": moved_kg,
            },
        )
        self.db.flush()
        return {
            "source_box_id": source_box_id,
            "target_box_id": target_box_id,
            "moved_packs": quantity_to_move,
            "moved_net_kg": moved_kg,
        }

    def sync_transportation_manifest(
        self,
        route_code: str,
        shipment_date: str,
        flight_carrier: str | None = None,
    ) -> TransportationManifest:
        """
        Automatically syncs finalized packing lists to generate accurate
        transportation manifests / freight invoices for shipping container management.
        """
        ref = f"TRN-{shipment_date.replace('-', '')}-{route_code}"
        manifest = (
            self.db.query(TransportationManifest)
            .filter(TransportationManifest.manifest_reference == ref)
            .first()
        )

        boxes = (
            self.db.query(Box)
            .filter(Box.route_code == route_code, Box.shipment_date == shipment_date, Box.is_locked.is_(True))
            .all()
        )

        total_boxes = len(boxes)
        total_gross = 0.0
        total_net = 0.0
        total_vol = 0.0

        for b in boxes:
            total_gross += float(b.actual_gross_weight_kg or 0.0)
            total_net += float(b.actual_net_weight_kg or 0.0)

            # Determine dimensions
            if b.shipping_dimensions_snapshot:
                length, width, height = b.shipping_dimensions_snapshot
            elif b.custom_length_cm and b.custom_width_cm and b.custom_height_cm:
                length, width, height = (
                    float(b.custom_length_cm),
                    float(b.custom_width_cm),
                    float(b.custom_height_cm),
                )
            elif b.override_box_type:
                length, width, height = (
                    float(b.override_box_type.length_cm),
                    float(b.override_box_type.width_cm),
                    float(b.override_box_type.height_cm),
                )
            elif b.box_type:
                length, width, height = (
                    float(b.box_type.length_cm),
                    float(b.box_type.width_cm),
                    float(b.box_type.height_cm),
                )
            else:
                length, width, height = 38.0, 32.0, 18.0

            vol = volumetric_weight_kg(length, width, height)
            total_vol += vol

        chargeable = max(total_gross, total_vol)

        if not manifest:
            manifest = TransportationManifest(
                manifest_reference=ref,
                route_code=route_code,
                shipment_date=shipment_date,
                flight_carrier=flight_carrier,
                total_boxes_count=total_boxes,
                total_gross_weight_kg=round(total_gross, 2),
                total_net_weight_kg=round(total_net, 2),
                total_volumetric_weight_kg=round(total_vol, 2),
                total_chargeable_weight_kg=round(chargeable, 2),
                status="LOCKED",
            )
            self.db.add(manifest)
        else:
            manifest.flight_carrier = flight_carrier
            manifest.total_boxes_count = total_boxes
            manifest.total_gross_weight_kg = round(total_gross, 2)
            manifest.total_net_weight_kg = round(total_net, 2)
            manifest.total_volumetric_weight_kg = round(total_vol, 2)
            manifest.total_chargeable_weight_kg = round(chargeable, 2)
            manifest.status = "LOCKED"

        self.db.flush()
        self.db.refresh(manifest)
        return manifest

    def transfer_box_contents(
        self,
        source_box_id: str,
        target_box_id: str,
        item_ids: list[str] | None = None,
        remove_empty_source: bool = False,
        *,
        actor: User,
    ) -> dict[str, Any]:
        """
        Smart Box Resizing Workflow:
        Transfers items from smaller boxes into a larger box (e.g., combining Small and Medium into Large).
        Transfers item data, product IDs, pack counts, and weights automatically without manual re-typing.
        """
        if source_box_id == target_box_id:
            raise ValueError("Source and target boxes must be different")
        source_box = self._editable_box(source_box_id)
        target_box = self._editable_box(target_box_id)
        if not source_box or not target_box:
            raise ValueError("Source or target box not found")
        if source_box.is_locked or target_box.is_locked:
            raise ValueError("Cannot transfer items between locked boxes")
        if source_box.client_order_id != target_box.client_order_id:
            raise ValueError("Box transfers must stay within the same client order")
        _require_editable(source_box.order)
        if (
            source_box.shipment_date != target_box.shipment_date
            or source_box.route_code != target_box.route_code
        ):
            raise ValueError("Box transfers must stay within the same shipment route and date")

        # Find items to transfer
        q = self.db.query(BoxItem).filter(BoxItem.box_id == source_box_id)
        if item_ids:
            q = q.filter(BoxItem.id.in_(item_ids))
        items_to_move = q.all()

        if not items_to_move:
            raise ValueError("No items found in source box to transfer")

        target_material = (target_box.override_box_type or target_box.box_type).box_type
        from app.modules.packing.materials import require_material
        for item in items_to_move:
            if item.product:
                require_material(item.product, target_material, target_box.order)
        target_existing_weight = sum(float(item.final_net_weight_kg) for item in target_box.items)
        transfer_weight = sum(float(item.final_net_weight_kg) for item in items_to_move)
        target_capacity = float(
            (target_box.override_box_type or target_box.box_type).max_safe_net_weight_kg
        )
        if target_existing_weight + transfer_weight > target_capacity + 0.01:
            raise ValueError("Transfer would exceed the target box safe capacity")

        contents = [(i.product, i.pack_size, i.final_packed_count, float(i.final_net_weight_kg)) for i in target_box.items]
        incoming = [(i.product, i.pack_size, i.final_packed_count, float(i.final_net_weight_kg)) for i in items_to_move]
        validate_contents(target_box, [*contents, *incoming])
        transferred_count = 0
        transferred_weight = 0.0

        for s_item in items_to_move:
            qty = s_item.final_packed_count
            wt = float(s_item.final_net_weight_kg)

            # Check if target box already has this produce SKU and pack size
            existing_target_item = (
                self.db.query(BoxItem)
                .filter(
                    BoxItem.box_id == target_box_id,
                    BoxItem.product_id == s_item.product_id,
                    BoxItem.pack_size == s_item.pack_size,
                )
                .first()
            )

            if existing_target_item:
                existing_target_item.final_packed_count += qty
                existing_target_item.final_net_weight_kg = round(
                    float(existing_target_item.final_net_weight_kg) + wt, 4
                )
                existing_target_item.adjustment_type = AdjustmentType.QUANTITY_INCREASED
                existing_target_item.adjustment_notes = (
                    f"Combined from Box #{source_box.box_number}"
                )
                self.db.delete(s_item)
            else:
                # Reassign item directly to target box
                s_item.box_id = target_box_id
                s_item.adjustment_type = AdjustmentType.QUANTITY_INCREASED
                s_item.adjustment_notes = f"Transferred from Box #{source_box.box_number}"

            transferred_count += qty
            transferred_weight = round(transferred_weight + wt, 2)

        # Recalculate weights on both boxes
        self.db.flush()
        source_remaining = self.db.query(BoxItem).filter(BoxItem.box_id == source_box_id).all()
        target_all = self.db.query(BoxItem).filter(BoxItem.box_id == target_box_id).all()

        # Content changes invalidate cached totals; finalization derives them again.
        _clear_weights(source_box, target_box)

        source_box_removed = False
        if remove_empty_source and len(source_remaining) == 0:
            self.db.delete(source_box)
            source_box_removed = True

        self._audit(
            actor,
            "BOX_CONTENTS_TRANSFERRED",
            target_box,
            {"source_box_id": source_box_id},
            {
                "transferred_pack_count": transferred_count,
                "transferred_net_weight_kg": transferred_weight,
                "totals_recalculated_at_finalization": True,
            },
        )

        self.db.flush()

        return {
            "source_box_id": source_box_id,
            "target_box_id": target_box_id,
            "transferred_items_count": len(items_to_move),
            "transferred_pack_count": transferred_count,
            "transferred_net_weight_kg": transferred_weight,
            "target_box_new_net_kg": round(
                sum(float(item.final_net_weight_kg) for item in target_all), 2
            ),
            "source_box_remaining_items": len(source_remaining),
            "source_box_removed": source_box_removed,
        }

    def generate_vehicle_loading_export(
        self, route_code: str, shipment_date: str
    ) -> dict[str, Any]:
        """
        Generates a vehicle loading summary file that outputs the exact number of boxes per customer
        (e.g., 'Tete: 10 boxes', 'BE-DUYÊN: 3 boxes') for floor staff to double-check vehicle load totals.
        """
        boxes = (
            self.db.query(Box)
            .filter(Box.route_code == route_code, Box.shipment_date == shipment_date, Box.is_locked.is_(True))
            .order_by(Box.box_number)
            .all()
        )

        customer_counts: dict[str, dict[str, Any]] = {}
        total_boxes = len(boxes)
        total_gross = 0.0
        total_net = 0.0

        for b in boxes:
            cust_name = (
                b.order.customer.company_name if (b.order and b.order.customer) else "General"
            )
            cust_code = b.order.customer.client_code if (b.order and b.order.customer) else "GEN"
            key = cust_code
            if key not in customer_counts:
                customer_counts[key] = {
                    "customer_code": cust_code,
                    "company_name": cust_name,
                    "boxes_count": 0,
                    "box_numbers": [],
                    "total_net_kg": 0.0,
                    "total_gross_kg": 0.0,
                }
            customer_counts[key]["boxes_count"] += 1
            customer_counts[key]["box_numbers"].append(b.box_number)
            net_wt = float(b.actual_net_weight_kg or 0.0)
            gross_wt = float(b.actual_gross_weight_kg or 0.0)
            customer_counts[key]["total_net_kg"] = round(
                customer_counts[key]["total_net_kg"] + net_wt, 2
            )
            customer_counts[key]["total_gross_kg"] = round(
                customer_counts[key]["total_gross_kg"] + gross_wt, 2
            )
            total_net += net_wt
            total_gross += gross_wt

        lines = [
            "=" * 60,
            "VEHICLE LOADING AND BOX DISPATCH CHECKLIST",
            f"Route: {route_code} | Shipment Target Date: {shipment_date}",
            "=" * 60,
            f"{'Customer Breakdown':<35} | {'Boxes':<10} | {'Box Range':<15}",
            "-" * 60,
        ]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Customer Code", "Company Name", "Box ID", "Box Number", "Boxes Count", "Box Net Weight (kg)", "Box Gross Weight (kg)", "SKU", "Product", "Pack Size", "Packed Count", "Item Net Weight (kg)"])
        box_contents = []
        for box in boxes:
            customer = box.order.customer
            contents = []
            for item in box.items:
                row = {"product_code": item.product.t_code, "product_name": item.product.name_en,
                       "pack_size": item.pack_size, "packed_count": item.final_packed_count,
                       "net_weight_kg": float(item.final_net_weight_kg)}
                contents.append(row)
                values = [customer.client_code, customer.company_name, box.id, box.box_number,
                    customer_counts[customer.client_code]["boxes_count"], float(box.actual_net_weight_kg or 0),
                    float(box.actual_gross_weight_kg or 0), *row.values()]
                writer.writerow(["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value for value in values])
            box_contents.append({"box_id": box.id, "box_number": box.box_number, "customer_code": customer.client_code, "items": contents})

        for _, data in sorted(customer_counts.items(), key=lambda item: item[0]):
            box_num_str = (
                f"#{min(data['box_numbers'])}-#{max(data['box_numbers'])}"
                if len(data["box_numbers"]) > 1
                else f"#{data['box_numbers'][0]}"
                if data["box_numbers"]
                else ""
            )
            summary_label = f"{data['customer_code']}: {data['boxes_count']} boxes"
            lines.append(
                f"{summary_label:<35} | {data['boxes_count']:>4} boxes | {box_num_str:<15}"
            )

        lines.extend(
            [
                "-" * 60,
                f"TOTAL VEHICLE LOAD: {total_boxes} boxes | Gross Weight: {round(total_gross, 2)} kg | Net Weight: {round(total_net, 2)} kg",
                "=" * 60,
                "Verified by Floor Lead: ______________________ Date/Time: ____________",
            ]
        )

        return {
            "route_code": route_code,
            "shipment_date": shipment_date,
            "total_boxes_count": total_boxes,
            "total_gross_weight_kg": round(total_gross, 2),
            "total_net_weight_kg": round(total_net, 2),
            "customers": list(customer_counts.values()),
            "checklist_text": "\n".join(lines),
            "csv_content": output.getvalue(),
            "boxes": box_contents,
        }

    def fast_track_template_pack_order(self, client_order_id: str, actor: User, packing_mode: str | None = None) -> dict[str, Any]:
        """Template packing: one box of the ordered lines, template weights, locked, Handoff 02 sealed."""
        require(actor, Action.LOCK_FINAL_PACK)
        order = (
            self.db.query(ClientOrder)
            .filter(ClientOrder.id == client_order_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise ValueError(f"ClientOrder {client_order_id} not found")

        from app.core import work_limits as limits
        from app.core.work_limits import WorkLimit
        from app.modules.packing.planning import input_scope
        input_scope(self.db, order)
        if sum(line.confirmed_pack_count if line.confirmed_pack_count is not None else line.ordered_pack_count
               for line in order.items) > limits.MAX_PACKS:
            raise WorkLimit("Template packing exceeds the 250000-pack order budget")

        boxes = self._order_boxes(order.id)
        if settings.PACKING_PROFILES_ENABLED and packing_mode not in PACKING_MODES:
            raise ValueError("Select EL or Non-EL rules; template packing also requires reviewed profiles")
        if any(box.is_locked for box in boxes):
            raise ValueError("Cannot fast-track locked packing; use the finalized packing record")
        for existing_box in boxes:
            require_current(existing_box)
        if settings.PACKING_PROFILES_ENABLED and not boxes:
            from app.modules.packing.planning import generate_plan, lock_inputs, persist_plan
            lock_inputs(self.db, order)
            plan = generate_plan(self.db, order, packing_mode, template=True)
            boxes = persist_plan(self.db, order, plan, packing_mode)
        if not boxes:
            template_rows = []
            for line in order.items:
                packs = (
                    line.confirmed_pack_count
                    if line.confirmed_pack_count is not None
                    else line.ordered_pack_count
                )
                if packs == 0:
                    continue
                if packs is None or packs < 0:
                    raise ValueError("Template quantities require a nonnegative pack count")
                kg = float(
                    line.confirmed_net_weight_kg
                    if line.confirmed_net_weight_kg is not None
                    else line.estimated_net_weight_kg
                    if line.estimated_net_weight_kg is not None
                    else packs * pack_weight_kg(line.pack_size)
                )
                if not math.isfinite(kg) or round(kg, 4) <= 0:
                    raise ValueError("Positive template packs require finite positive net weight")
                template_rows.append((line, packs, kg))
            if not template_rows:
                raise ValueError("At least one fulfilled item is required to generate boxes")
            catalogue = next((candidate for candidate in
                self.db.query(BoxCatalogue).filter(BoxCatalogue.is_active.is_(True)).all()
                if reference_for(candidate)), None)
            if not catalogue:
                raise ValueError("No active BoxCatalogue available in master data")
            box = new_box(order, catalogue.id, 1)
            box.packing_mode = packing_mode or "EL"
            self.db.add(box)
            self.db.flush()
            for line, packs, kg in template_rows:
                self.db.add(
                    BoxItem(
                        box_id=box.id,
                        product_id=line.packing_product.id,
                        pack_size=line.pack_size or "std",
                        final_packed_count=packs,
                        final_net_weight_kg=round(kg, 4),
                        adjustment_type=AdjustmentType.AS_ORDERED,
                    )
                )
            self.db.flush()
            self.db.refresh(box)
            boxes = [box]

        now = datetime.now(UTC)
        for b in boxes:
            validate_contents(b)
            b.packing_mode = packing_mode or b.packing_mode or "EL"
            b.packing_snapshot = snapshot_for(b)
            catalogue = b.override_box_type or b.box_type
            b.shipping_dimensions_snapshot = [float(catalogue.length_cm), float(catalogue.width_cm), float(catalogue.height_cm)]
            net = round(sum(float(item.final_net_weight_kg) for item in b.items), 2)
            capacity = float((b.override_box_type or b.box_type).max_safe_net_weight_kg)
            if net > capacity + 0.01:
                raise ValueError("Template contents exceed the box safe capacity; use normal packing")
            derive_box_weights(b)
            b.weight_utilization_pct = round(net / capacity * 100, 2)
            b.packed_by_staff = actor.id
            b.is_locked = True
            b.locked_at = now

        order.packing_approval_status = "APPROVED"
        order.packing_review_note = "Template packing approved by Owner/Admin"

        WorkflowService(self.db).fast_track_to_final_pack(
            target=order, actor=actor, target_table="client_orders"
        )
        handoff = HandoffService(self.db).seal_handoff_02(order=order, actor=actor)
        self.db.flush()

        return {
            "order_id": order.id,
            "order_ref_id": order.order_ref_id,
            "status": order.status.value,
            "boxes_count": len(boxes),
            "total_net_weight_kg": sum(float(b.actual_net_weight_kg) for b in boxes),
            "total_gross_weight_kg": sum(float(b.actual_gross_weight_kg) for b in boxes),
            "handoff_sha256": handoff.payload_sha256,
        }

    def fast_track_template_pack_group(
        self, group_name: str, shipment_date: str | None, actor: User, packing_mode: str | None = None
    ) -> dict[str, Any]:
        """Fast-tracks every order in a route group (e.g. GROUP CT, GROUP BE, or ALL)."""
        results = [
            self.fast_track_template_pack_order(order.id, actor, packing_mode=packing_mode)
            for order in orders_in_group(self.db, group_name, shipment_date)
        ]
        return {
            "group_name": group_name,
            "orders_packed": len(results),
            "total_boxes": sum(r["boxes_count"] for r in results),
            "total_net_weight_kg": round(sum(r["total_net_weight_kg"] for r in results), 2),
            "orders": results,
        }
