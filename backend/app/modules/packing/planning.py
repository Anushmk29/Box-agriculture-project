"""Reviewed planning and expiring, concurrency-checked replan previews."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from app.core import work_limits as limits
from app.core.config import settings
from app.core.work_limits import WorkLimit
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.models.order import ClientOrderItem, OrderStatus
from app.models.packing import Box, BoxItem
from app.models.packing_evidence import PackingHistoryReview
from app.modules.orders.quantities import weight_only_line
from app.modules.packing.boxes import new_box, order_route_code
from app.modules.packing.catalogue import reference_for
from app.modules.packing.history import allocate_history, history_basis
from app.modules.packing.materials import material_policy, preferred_material
from app.modules.packing.optimizer import (
    DeterministicPackingOptimizer,
    PackingDemand,
    PackingOption,
    PackingPlan,
)
from app.modules.packing.profiles import (
    PACKING_MODES,
    box_volume,
    profile_for,
    require_known_el,
    snapshot_for,
    unit_volume,
)


def input_scope(db, order):
    items = db.query(ClientOrderItem.product_id, ClientOrderItem.replacement_product_id).filter_by(
        client_order_id=order.id).limit(limits.MAX_DEMANDS + 1).all()
    floor = db.query(BoxItem.product_id).join(Box).filter(Box.client_order_id == order.id).limit(limits.MAX_ITEMS + 1).all()
    boxes = db.query(Box.box_catalogue_id, Box.override_box_catalogue_id).filter_by(
        client_order_id=order.id).limit(limits.MAX_BOXES + 1).all()
    if len(items) > limits.MAX_DEMANDS or len(floor) > limits.MAX_ITEMS or len(boxes) > limits.MAX_BOXES:
        raise WorkLimit("Order packing exceeds its item/box work budget; split or review the order")
    pids = {pid for row in items for pid in row if pid} | {pid for (pid,) in floor}
    if len(pids) > 2 * limits.MAX_DEMANDS:
        raise WorkLimit("Order packing exceeds its product scope budget")
    cids = {cid for row in boxes for cid in row if cid}
    return pids, cids


def catalogue_scope(db, products, catalogue_ids):
    return db.query(BoxCatalogue).filter(
        (BoxCatalogue.is_active.is_(True) & BoxCatalogue.box_type.in_({preferred_material(p) for p in products} | {p.box_material for p in products}))
        | BoxCatalogue.id.in_(catalogue_ids)).order_by(BoxCatalogue.id)


def lock_inputs(db, order):
    # Shared reads allow independent plans to use the same catalogue. Setup
    # writers retain exclusive product/box locks, in the same sorted order.
    product_ids, catalogue_ids = input_scope(db, order)
    products = (db.query(Product).filter(Product.id.in_(product_ids)).order_by(Product.id)
        .with_for_update(read=True).populate_existing().all())
    for product in products:
        db.expire(product, ["packing_profiles", "box_limits"])
    catalogues = catalogue_scope(db, products, catalogue_ids).limit(limits.MAX_OPTIONS + 1).with_for_update(read=True).populate_existing().all()
    if len(catalogues) > limits.MAX_OPTIONS:
        raise WorkLimit("Too many active box options for one order; review the catalogue")
    return products, catalogues


def generate_plan(db: Session, order, mode: str, *, from_floor=False, template=False):
    input_scope(db, order)
    if mode not in PACKING_MODES:
        raise ValueError("Select EL or Non-EL packing rules before generating boxes")
    rows = []
    if from_floor:
        totals = defaultdict(lambda: [0, 0.0])
        items = db.query(BoxItem).join(Box).filter(Box.client_order_id == order.id).all()
        for item in items:
            key = (item.product_id, item.pack_size)
            totals[key][0] += item.final_packed_count
            totals[key][1] += float(item.final_net_weight_kg)
        products = {
            p.id: p
            for p in db.query(Product).filter(Product.id.in_({key[0] for key in totals})).all()
        }
        rows = [
            (products[pid], size, count, net)
            for (pid, size), (count, net) in sorted(totals.items())
        ]
    else:
        for item in order.items:
            product = item.packing_product
            if template:
                count = (
                    item.confirmed_pack_count
                    if item.confirmed_pack_count is not None
                    else item.ordered_pack_count
                )
                if not count:
                    continue
                profile = None if history_basis(product) else profile_for(product, item.pack_size)
                net = (
                    item.confirmed_net_weight_kg
                    if item.confirmed_net_weight_kg is not None
                    else item.estimated_net_weight_kg
                )
                if net is None and profile:
                    net = count * float(profile.net_weight_kg)
                rows.append((product, item.pack_size, count, float(net or 0)))
                continue
            if (item.ordered_pack_count == 0 and not weight_only_line(item)) or item.confirmed_pack_count == 0:
                continue
            if item.confirmed_pack_count is None or item.confirmed_net_weight_kg is None:
                raise ValueError("Handoff 01 confirmed quantities are required for packing")
            rows.append(
                (
                    product,
                    item.pack_size,
                    item.confirmed_pack_count,
                    float(item.confirmed_net_weight_kg),
                )
            )
    if not rows:
        raise ValueError("At least one packed item is required to generate boxes")
    catalogues = (db.query(BoxCatalogue).filter(BoxCatalogue.is_active.is_(True),
        BoxCatalogue.box_type.in_({preferred_material(product) for product, _, _, _ in rows}))
        .order_by(BoxCatalogue.id).limit(limits.MAX_OPTIONS + 1).with_for_update(read=True).populate_existing().all())
    catalogues = [c for c in catalogues if reference_for(c)]
    if settings.PACKING_PROFILES_ENABLED:
        catalogues = [c for c in catalogues if c.packing_reviewed]
    if not catalogues:
        raise ValueError(
            "Review the safe capacity of an active workbook box in Packing setup"
        )
    options = [
        PackingOption(c.id, c.box_type, float(c.max_safe_net_weight_kg), box_volume(c))
        for c in catalogues
    ]
    historical_boxes, rows = allocate_history(db, rows, options, route_code=order_route_code(order), mode=mode, order=order)
    demands, missing = [], []
    for product, size, count, net in rows:
        try:
            if history_basis(product):
                raise ValueError(f"{product.t_code} {size}: no exact reviewed historical combination covers the remaining packs; review a template or choose reviewed volume estimates")
            require_known_el(product, mode)
            profile_for(product, size)
            volume = unit_volume(product, size, net / count if count else None)
        except ValueError as exc:
            missing.append(str(exc))
            continue
        demands.append(
            PackingDemand(
                product.id,
                size,
                count,
                net / count,
                preferred_material(product),
                product.temperature,
                product.control_status,
                product.produce_type,
                product.weight_class,
                product.botanical_family,
                volume,
                product.firmness,
                {
                    limit.box_catalogue_id: float(limit.max_net_weight_kg)
                    for limit in product.box_limits
                    if limit.reviewed
                },
            )
        )
    if missing:
        raise ValueError("Packing profiles need review: " + "; ".join(missing))
    if not demands:
        return PackingPlan(order_route_code(order), historical_boxes)
    plan = DeterministicPackingOptimizer().solve(
        demands=demands, options=options, route_code=order_route_code(order), packing_mode=mode
    )
    if len(historical_boxes) + len(plan.boxes) > limits.MAX_BOXES:
        raise WorkLimit("Combined packing plan exceeds its box work budget")
    return PackingPlan(plan.route_code, [*historical_boxes, *plan.boxes])


def plan_summary(plan):
    return [
        {
            "box_number": number,
            "box_catalogue_id": box.option.id,
            "net_weight_kg": box.total_net_weight_kg,
            "volume_cm3": box.total_volume_cm3,
            "usable_volume_cm3": box.option.usable_volume_cm3,
            "history_evidence": box.history_evidence,
            "items": [
                {
                    "product_id": i.product_id,
                    "pack_size": i.pack_size,
                    "count": i.pack_count,
                    "net_weight_kg": i.net_weight_kg,
                }
                for i in box.items
            ],
        }
        for number, box in enumerate(plan.boxes, 1)
    ]


def persist_plan(db, order, plan, mode):
    old_boxes = db.query(Box).filter_by(client_order_id=order.id).all()
    if any(b.is_locked for b in old_boxes):
        raise ValueError("Locked packing cannot be replaced")
    order.packing_revision += 1
    for box in old_boxes:
        db.delete(box)
    db.flush()
    products = {
        p.id: p
        for p in db.query(Product)
        .filter(Product.id.in_({i.product_id for b in plan.boxes for i in b.items}))
        .all()
    }
    catalogues = {c.id: c for c in db.query(BoxCatalogue).filter(
        BoxCatalogue.id.in_({box.option.id for box in plan.boxes})).all()}
    boxes = []
    for number, planned in enumerate(plan.boxes, 1):
        box = new_box(order, planned.option.id, number, route_code=plan.route_code)
        box.box_type = catalogues[planned.option.id]
        box.packing_mode = mode
        box.is_mixed_box = len({i.product_id for i in planned.items}) > 1
        for item in planned.items:
            box.items.append(
                BoxItem(
                    product=products[item.product_id],
                    pack_size=item.pack_size,
                    final_packed_count=item.pack_count,
                    final_net_weight_kg=item.net_weight_kg,
                )
            )
        db.add(box)
        box.packing_snapshot = snapshot_for(box, history_evidence=planned.history_evidence)
        boxes.append(box)
    db.flush()
    return boxes


def fingerprint(db, order):
    _, catalogue_ids = input_scope(db, order)
    boxes = (
        db.query(Box).filter_by(client_order_id=order.id).order_by(Box.id).populate_existing().all()
    )
    pids = {item.product_id for box in boxes for item in box.items}
    state = {
        "history_reviews": [(review.id, review.revision, review.status, review.source_version)
                            for review in db.query(PackingHistoryReview).order_by(PackingHistoryReview.id).limit(2001)],
        "order_id": order.id,
        "packing_revision": order.packing_revision,
        "status": order.status.value,
        "boxes": [
            {
                "id": b.id,
                "mode": b.packing_mode,
                "locked": b.is_locked,
                "catalogue": b.override_box_catalogue_id or b.box_catalogue_id,
                "dimensions": [
                    str(b.custom_length_cm),
                    str(b.custom_width_cm),
                    str(b.custom_height_cm),
                ],
                "items": sorted(
                    (
                        i.id,
                        i.product_id,
                        i.pack_size,
                        i.final_packed_count,
                        str(i.final_net_weight_kg),
                    )
                    for i in b.items
                ),
            }
            for b in boxes
        ],
        "products": [
            {
                "id": p.id,
                "material_policy": material_policy(p, order),
                "revision": p.packing_revision,
                "active": p.is_active,
                "volume": [str(p.packing_volume_l_per_kg), p.packing_volume_reviewed, p.packing_volume_estimated],
                "classification": [
                    p.packing_reviewed,
                    p.firmness,
                    p.control_status.value,
                    p.temperature.value,
                    p.box_material.value,
                    p.produce_type.value,
                    p.weight_class.value,
                    p.botanical_family,
                ],
                "profiles": sorted(
                    (
                        v.id,
                        v.pack_key,
                        str(v.net_weight_kg),
                        str(v.volume_cm3),
                        v.reviewed,
                        v.revision,
                        sorted(v.aliases or []),
                        json.dumps(v.evidence or {}, sort_keys=True),
                    )
                    for v in p.packing_profiles
                ),
                "limits": sorted(
                    (v.box_catalogue_id, str(v.max_net_weight_kg), v.reviewed) for v in p.box_limits
                ),
            }
            for p in db.query(Product).filter(Product.id.in_(pids)).order_by(Product.id)
        ],
        "catalogues": [
            (
                c.id,
                c.packing_revision,
                c.is_active,
                c.packing_reviewed,
                c.box_type.value,
                str(c.max_safe_net_weight_kg),
                str(c.length_cm),
                str(c.width_cm),
                str(c.height_cm),
            )
            for c in catalogue_scope(db, db.query(Product).filter(Product.id.in_(pids)).all(), catalogue_ids).limit(limits.MAX_OPTIONS + 1)
        ],
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def preview_replan(db, order, mode):
    if order.status not in {OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING}:
        raise ValueError("Only unfinished packing can be replanned")
    lock_inputs(db, order)
    plan = generate_plan(db, order, mode, from_floor=True)
    token = jwt.encode(
        {
            "kind": "packing_replan",
            "order_id": order.id,
            "mode": mode,
            "fingerprint": fingerprint(db, order),
            "plan_digest": hashlib.sha256(json.dumps(plan_summary(plan), sort_keys=True).encode()).hexdigest(),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    summary = plan_summary(plan)
    names = {
        p.id: p
        for p in db.query(Product)
        .filter(Product.id.in_({i.product_id for b in plan.boxes for i in b.items}))
        .all()
    }
    for box in summary:
        for item in box["items"]:
            product = names[item["product_id"]]
            item["product_name"] = " / ".join(name for name in [product.name_th, product.name_vi, product.name_en] if name and name.strip())
    return {
        "preview_token": token,
        "packing_mode": mode,
        "boxes": summary,
        "warning": "Applying replaces box assignments and stickers. Current packed products, counts and net weights are preserved.",
    }


def apply_replan(db, order, token, actor):
    try:
        data = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise ValueError("This preview expired or is invalid. Preview the plan again") from exc
    if data.get("kind") != "packing_replan" or data.get("order_id") != order.id:
        raise ValueError("This preview belongs to another order")
    if order.status not in {OrderStatus.PACKING_DESIGNED, OrderStatus.FLOOR_PACKING}:
        raise ValueError("Only unfinished packing can be replanned")
    lock_inputs(db, order)
    if fingerprint(db, order) != data.get("fingerprint"):
        raise ValueError("Packing or setup data changed. Preview again before replacing boxes")
    plan = generate_plan(db, order, data["mode"], from_floor=True)
    if hashlib.sha256(json.dumps(plan_summary(plan), sort_keys=True).encode()).hexdigest() != data.get("plan_digest"):
        raise ValueError("The suggested plan changed; preview it again before applying")
    boxes = persist_plan(db, order, plan, data["mode"])
    db.add(
        AuditLog(
            event_type="PACKING_REPLANNED",
            actor_id=actor.id,
            actor_role=actor.role.value,
            target_table="client_orders",
            target_id=order.id,
            after_state={"box_count": len(boxes), "packing_mode": data["mode"]},
        )
    )
    db.flush()
    return boxes
