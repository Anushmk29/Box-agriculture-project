"""Source evidence, explicit human approvals, and exact historical fit checks."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import monotonic

from app.core.packs import lookup_pack_key, pack_key
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.models.packing_evidence import PackingHistoryReview
from app.modules.packing.catalogue import reference_for


@lru_cache
def history_data():
    path = Path(__file__).resolve().parents[2] / "assets/packing_history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "version": "", "examples": [], "documents": [], "summary": {}, "extraction_issues": []}


def history_basis(product):
    return (product.packing_source or {}).get("table", {}).get("planning_basis", "VOLUME") == "HISTORY"


def source_current(review):
    # Adding another shipment must not withdraw unchanged, content-addressed examples.
    return review.example_id in _source_ids(history_data()["version"])


@lru_cache(maxsize=8)
def _source_ids(version):
    return frozenset(example["id"] for example in history_data()["examples"])


def validate_rules(rows, catalogue, mode, *, order=None, require_reviewed=True):
    """Shared hard constraints, without asserting a volume for historical contents."""
    from app.modules.packing.materials import require_material
    from app.modules.packing.optimizer import PackingDemand
    from app.modules.packing.profiles import product_limit, require_known_el

    if mode not in {"EL", "NON_EL"}:
        raise ValueError("Choose EL or Non-EL rules")
    if not catalogue.is_active or not catalogue.packing_reviewed or not reference_for(catalogue):
        raise ValueError("Review an active workbook box before using packing history")
    capacity = float(catalogue.max_safe_net_weight_kg)
    if not math.isfinite(capacity) or capacity <= 0:
        raise ValueError("Box safe weight must be finite and positive")
    keys, total = set(), 0.0
    for product, size, count, net in rows:
        if not product or not product.is_active or type(count) is not int or count <= 0 or not math.isfinite(net) or net <= 0:
            raise ValueError("Each history item needs an active product, whole positive pack count and positive net kilograms")
        pack_key(size)
        if size.strip().casefold() in {"kg", "g", "kilogram", "gram"}:
            raise ValueError("Loose weight needs a confirmed physical pack format, for example 1pc or a labelled bag")
        table = (product.packing_source or {}).get("table", {})
        if table.get("unresolved") or product.firmness not in {"HARD", "SEMI_HARD", "SOFT"}:
            raise ValueError(f"{product.t_code}: resolve grouping classifications, limits and firmness first")
        if require_reviewed and not product.packing_reviewed:
            raise ValueError(f"{product.t_code}: confirm product settings before planning")
        require_known_el(product, mode)
        require_material(product, catalogue.box_type, order)
        demand = PackingDemand(product.id, size, count, net / count, catalogue.box_type,
                               product.temperature, product.control_status, product.produce_type,
                               product.weight_class, product.botanical_family, 1)
        keys.add(demand.compatibility_key("", mode))
        total += net
    if len(keys) > 1:
        raise ValueError("Historical combination conflicts with current temperature, control, produce type or weight-class rules")
    if total > capacity + .0001:
        raise ValueError("Historical combination exceeds the box safe weight")
    if rows and len({row[0].id for row in rows}) == 1:
        limit = product_limit(rows[0][0], catalogue.id)
        if limit is not None and total > limit + .0001:
            raise ValueError("Historical combination exceeds the approved single-product box limit")
    return next(iter(keys), ())


def box_candidates(name, boxes):
    compact = "".join(name.split()).replace("Ì", "๋")
    material = "FOAM" if compact.startswith("ฟ.") else "CARDBOARD" if compact.startswith("ก.") else None
    thai = compact[2:]
    return [box for box in boxes if (ref := reference_for(box)) and ref["box_type"] == material
            and "".join(re.sub(r"^[กฟ][.\s]+", "", ref["name_th"].strip()).split()) == thai]


def example_out(example, *, index, boxes, review=None):
    items = []
    for raw in example["items"]:
        match = index.match(raw, raw_code=raw["code"])
        candidates = [index.products[identity] for identity in match.candidate_ids]
        items.append({**raw, "product_id": match.product.id if match.product else "",
                      "matching": match.evidence(), "candidates": [
                          {"id": p.id, "t_code": p.t_code, "name_th": p.name_th,
                           "name_vi": p.name_vi, "name_en": p.name_en} for p in candidates]})
    choices = box_candidates(example["box_name"], boxes)
    return {**example, "items": items,
            "box_catalogue_id": choices[0].id if len(choices) == 1 else "",
            "box_candidates": [box.id for box in choices],
            "review": {**review_out(review), "approval_issues": approval_issues(review, index.products, boxes)} if review else None}


def approval_issues(review, products, boxes):
    if review.status != "APPROVED":
        return []
    issues = []
    if not source_current(review):
        issues.append("Source evidence changed")
    definition = review.definition
    catalogue = next((box for box in boxes if box.id == definition.get("box_catalogue_id")), None)
    if not catalogue or not catalogue.is_active or not catalogue.packing_reviewed or catalogue.packing_revision != definition.get("catalogue_revision"):
        issues.append("Box capacity settings changed or need review")
    for item in definition.get("items", []):
        product = products.get(item["product_id"])
        if not product or not product.packing_reviewed or product.packing_revision != item["product_revision"]:
            issues.append("Product settings changed or need confirmation")
            break
    return issues


def review_out(review):
    return {"id": review.id, "status": review.status, "revision": review.revision,
            "definition": review.definition, "note": review.note,
            "source_version": review.source_version,
            "source_current": source_current(review)}


def save_review(db, example_id, payload, actor):
    data = history_data()
    example = next((example for example in data["examples"] if example["id"] == example_id), None)
    if example is None:
        raise ValueError("Packing example is no longer in this source version")
    if payload.source_version != data["version"]:
        raise ValueError("Source evidence changed; reload the example")
    review = db.query(PackingHistoryReview).filter_by(example_id=example_id).with_for_update().populate_existing().first()
    if payload.expected_revision != (review.revision if review else 0):
        from app.core.revisions import RevisionConflict
        raise RevisionConflict("packing_history", example_id, review.revision if review else 0)
    definition = {}
    if payload.status == "APPROVED":
        if not payload.verified_fit:
            raise ValueError("Confirm that this exact combination was physically checked to fit")
        source_issues = example["issues"] or any(item["issues"] for item in example["items"])
        if source_issues and not payload.source_issues_acknowledged:
            raise ValueError("Resolve and acknowledge the source discrepancies in the review note")
        if len(payload.items) != len(example["items"]):
            raise ValueError("Map every source line; do not drop products from a historical box")
        product_ids = sorted({item.product_id for item in payload.items})
        products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).order_by(Product.id).with_for_update(read=True).all()}
        catalogue = db.query(BoxCatalogue).filter_by(id=payload.box_catalogue_id).with_for_update(read=True).first()
        if not catalogue:
            raise ValueError("Choose an approved box type")
        rows = [(products.get(item.product_id), item.pack_size.strip(), item.pack_count, item.net_weight_kg) for item in payload.items]
        validate_rules(rows, catalogue, payload.packing_mode)
        if len({(p.id, lookup_pack_key(size)) for p, size, *_ in rows}) != len(rows):
            raise ValueError("Repeated product/pack lines need source reconciliation before approval")
        definition = {"box_catalogue_id": catalogue.id, "catalogue_revision": catalogue.packing_revision,
                      "route_code": payload.route_code.strip().upper(), "packing_mode": payload.packing_mode,
                      "items": [{"product_id": p.id, "pack_size": size, "pack_key": lookup_pack_key(size),
                                 "pack_count": count, "net_weight_kg": net,
                                 "product_revision": p.packing_revision} for p, size, count, net in rows],
                      "source": example["source"], "pages": example["pages"],
                      "shipment": example["shipment"], "source_box_number": example["box_number"],
                      "verified_fit": True, "source_issues_acknowledged": payload.source_issues_acknowledged}
    before = review_out(review) if review else None
    if review is None:
        review = PackingHistoryReview(example_id=example_id, revision=0)
        db.add(review)
    review.source_version = data["version"]
    review.status, review.definition, review.note = payload.status, definition, payload.note.strip()
    review.revision += 1
    review.reviewed_by, review.updated_at = actor.id, datetime.now(UTC)
    db.flush()
    db.add(AuditLog(event_type="PACKING_HISTORY_REVIEWED", actor_id=actor.id,
                    actor_role=actor.role.value, target_table="packing_history_reviews",
                    target_id=review.id, before_state=before, after_state=review_out(review)))
    return review


def approved_reviews(db, *, route_code, mode):
    reviews = db.query(PackingHistoryReview).filter_by(status="APPROVED").order_by(PackingHistoryReview.id).limit(2001).with_for_update(read=True).populate_existing().all()
    if len(reviews) > 2000:
        raise ValueError("Too many approved packing examples; archive superseded guidance")
    return [review for review in reviews
            if source_current(review)
            and review.definition.get("route_code") == route_code.upper()
            and review.definition.get("packing_mode") == mode]


def matches_rows(review, rows, catalogue):
    definition = review.definition
    if definition.get("box_catalogue_id") != catalogue.id or definition.get("catalogue_revision") != catalogue.packing_revision:
        return False
    actual = defaultdict(lambda: [0, 0.0, None])
    for product, size, count, net in rows:
        values = actual[(product.id, lookup_pack_key(size))]
        values[0] += count
        values[1] += net
        values[2] = product
    expected = definition.get("items", [])
    if len(actual) != len(expected):
        return False
    for item in expected:
        found = actual.get((item["product_id"], item["pack_key"]))
        if (not found or found[0] != item["pack_count"] or abs(found[1] - item["net_weight_kg"]) > .0001
                or found[2].packing_revision != item["product_revision"]):
            return False
    return True


def fit_evidence(db, box, rows, catalogue):
    """Only an exact, currently approved recipe can replace a volume check."""
    if not rows or db is None:
        return None
    saved_id = ((box.packing_snapshot or {}).get("history_evidence") or {}).get("review_id")
    saved = db.query(PackingHistoryReview).filter_by(id=saved_id).with_for_update(read=True).populate_existing().first() if saved_id else None
    candidates = [saved] if (saved and saved.status == "APPROVED" and source_current(saved)
                              and saved.definition.get("route_code") == (box.route_code or "").upper()
                              and saved.definition.get("packing_mode") == box.packing_mode
                              and matches_rows(saved, rows, catalogue)) else approved_reviews(db, route_code=box.route_code or "", mode=box.packing_mode)
    for review in candidates:
        if matches_rows(review, rows, catalogue):
            validate_rules(rows, catalogue, box.packing_mode, order=box.order)
            return {"review_id": review.id, "revision": review.revision,
                    "example_id": review.example_id, "source_version": review.source_version,
                    "source": review.definition["source"], "pages": review.definition["pages"],
                    "basis": "HISTORICAL_VERIFIED"}
    return None


def allocate_history(db, rows, options, *, route_code, mode, order):
    """Reuse exact reviewed boxes; do not scale mixtures or invent occupied volume."""
    from app.core import work_limits
    from app.modules.packing.optimizer import PlannedBox, PlannedItem

    remaining = {}
    for product, size, count, net in rows:
        if type(count) is not int or count <= 0 or not math.isfinite(net) or net <= 0:
            raise ValueError("Confirmed physical pack counts and net weights must be positive before planning")
        key = (product.id, lookup_pack_key(size))
        if key in remaining:
            remaining[key][2] += count
            remaining[key][3] += net
        else:
            remaining[key] = [product, size, count, net]
    if sum(value[2] for value in remaining.values()) > work_limits.MAX_PACKS:
        from app.core.work_limits import WorkLimit
        raise WorkLimit("Packing exceeds the physical pack work budget; split the order")
    catalogues = {c.id: c for c in db.query(BoxCatalogue).filter(BoxCatalogue.id.in_([o.id for o in options])).all()}
    option_by_id = {option.id: option for option in options}
    reviews = sorted(approved_reviews(db, route_code=route_code, mode=mode),
                     key=lambda review: (-sum(item["pack_count"] for item in review.definition["items"]), review.id))
    boxes = []
    deadline, checks = monotonic() + 2, 0
    for review in reviews:
        checks += len(review.definition["items"])
        if checks > work_limits.MAX_PACKING_CHECKS or monotonic() > deadline:
            from app.core.work_limits import WorkLimit
            raise WorkLimit("Historical packing exceeded its work budget; narrow the approved guidance")
        definition = review.definition
        catalogue = catalogues.get(definition["box_catalogue_id"])
        if not catalogue or catalogue.packing_revision != definition["catalogue_revision"]:
            continue
        recipe = definition["items"]
        if not recipe or not all((item["product_id"], item["pack_key"]) in remaining for item in recipe):
            continue
        while True:
            checks += len(recipe)
            if checks > work_limits.MAX_PACKING_CHECKS or monotonic() > deadline:
                from app.core.work_limits import WorkLimit
                raise WorkLimit("Historical packing exceeded its work budget; narrow the approved guidance")
            contents = []
            for item in recipe:
                product, size, count, net = remaining[(item["product_id"], item["pack_key"])]
                if count < item["pack_count"] or product.packing_revision != item["product_revision"]:
                    break
                weight = net / count * item["pack_count"]
                if abs(weight - item["net_weight_kg"]) > .0001:
                    break
                contents.append((product, size, item["pack_count"], weight))
            if len(contents) != len(recipe):
                break
            try:
                key = validate_rules(contents, catalogue, mode, order=order)
            except ValueError:
                break
            if len(boxes) >= work_limits.MAX_BOXES:
                raise ValueError("Historical packing exceeds the box work budget")
            planned = PlannedBox(option_by_id[catalogue.id], key, history_evidence={
                "review_id": review.id, "revision": review.revision, "example_id": review.example_id,
                "source_version": review.source_version, "source": definition["source"],
                "pages": definition["pages"], "basis": "HISTORICAL_VERIFIED"})
            for product, size, count, net in contents:
                planned.items.append(PlannedItem(product.id, size, count, net / count,
                                                catalogue.box_type, product.temperature, product.control_status,
                                                product.produce_type, product.weight_class, None))
                value = remaining[(product.id, lookup_pack_key(size))]
                value[2] -= count
                value[3] = max(0, value[3] - net)
            boxes.append(planned)
    return boxes, [tuple(value) for value in remaining.values() if value[2] > 0]
