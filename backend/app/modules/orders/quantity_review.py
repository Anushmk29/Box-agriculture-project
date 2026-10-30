"""Review quantity problems while retaining the original source and an audit trail."""

import hashlib
import json

from sqlalchemy.orm import selectinload

from app.models.master_data import Product
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.intake import OrderIntake
from app.modules.orders.quantities import derive_quantity, unit_key
from app.modules.orders.templates import template_issues
from app.services.canonical_schema import ActionType


def draft_revision(drafts):
    encoded = json.dumps(
        [d.model_dump(mode="json") for d in drafts], sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def quantity_review_data(drafts, rows, *, offset=0, limit=50, issues_only=False):
    problems = template_issues(drafts)
    selected = [row for row in rows if row["issue"]] if issues_only else rows
    return {
        "templates": [d.metadata["template"] for d in drafts if d.metadata.get("template")],
        "template_issues": problems,
        "revision": draft_revision(drafts),
        "items": selected[offset:offset + limit],
        "total": len(rows),
        "filtered_total": len(selected),
        "offset": offset,
        "issues_only": issues_only,
        "has_more": offset + limit < len(selected),
        "issue_count": sum(bool(row["issue"]) for row in rows) + len(problems),
        "unknown_unit_count": sum(
            unit_key(row["unit"]) not in {"kg", "g", "pack", "pc", "box", "bag", "bunch", "tray"}
            for row in rows
        ),
    }


def quantity_rows(db, raw_file, drafts=None, *, resolver=None):
    drafts = order_drafts(raw_file) if drafts is None else drafts
    intake = OrderIntake(db, resolver=resolver)
    products = {}
    for draft in drafts:
        for item in draft.items:
            if item.action == ActionType.CANCEL or (item.quantity <= 0 and not item.quantity_issues):
                continue
            key = (item.product_code, item.product_name, tuple(item.alt_names))
            if key not in products:
                products[key] = intake.resolve(item, raw_file.main_customer_id)
    product_ids = {product.id for product in products.values() if product is not None}
    if product_ids:
        # Keep profile validation intact without one lazy database read per SKU.
        db.query(Product).filter(Product.id.in_(product_ids)).options(
            selectinload(Product.packing_profiles)
        ).all()
    rows = []
    for d_index, draft in enumerate(drafts):
        for index, item in enumerate(draft.items):
            if item.action == ActionType.CANCEL or (item.quantity <= 0 and not item.quantity_issues):
                continue
            key = (item.product_code, item.product_name, tuple(item.alt_names))
            try:
                quantity = derive_quantity(item, products[key])
                computed = {"pack_count": quantity.packs, "net_weight_kg": float(quantity.net_kg)}
                issue = None
            except ValueError as exc:
                computed = None
                issue = str(exc)
            rows.append(
                {
                    "row_id": f"{d_index}:{index}",
                    "client_code": draft.client_code,
                    "product_name": item.product_name or item.raw_text,
                    "product_code": item.product_code,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "pack_size": item.pack_size,
                    "net_weight_kg": item.net_weight_kg,
                    "source": item.source,
                    "computed": computed,
                    "issue": issue,
                }
            )
    return rows
