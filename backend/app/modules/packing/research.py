"""Staged reference evidence proposes drafts. It never approves a product or profile."""

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from app.models.master_data import ProductPackProfile
from app.modules.orders.quantities import label_weight
from app.modules.packing.profiles import pack_key
from app.modules.packing.reference import source_matches


@lru_cache
def research_data():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "assets/packing_research.json").read_text(
            encoding="utf-8"
        )
    )


def research_for(product, *, source_rows=None, additional_sizes=()):
    data = research_data()
    row_ids = (
        set(source_rows) if source_rows is not None else {r["row"] for r in source_matches(product)}
    )
    items = [r for r in data["items"] if r["row"] in row_ids]
    references = {r["id"]: r for r in data["references"]}
    density_ids = {r["density_id"] for r in items}
    selected = references.get(next(iter(density_ids))) if len(density_ids) == 1 else None
    proxy = any(r["density_status"] != "Commodity reference" for r in items)
    sizes = {}
    for value in [*(product.default_pack_sizes or []), *additional_sizes]:
        try:
            size = pack_key(value)
            if size not in {"kg", "g", "loose", "std"}:
                sizes[size] = {"label": value, "source": "Product pack label"}
        except ValueError:
            pass  # Ambiguous numeric labels need template context, not global guesses.
    for observation in data["pack_sizes"]:
        if (
            observation["group_row"] not in row_ids
            or observation["issue"]
            or not observation["grams"]
        ):
            continue
        raw = observation["raw"]
        try:
            size = pack_key(raw)
            if label_weight(size) is None:
                continue
        except ValueError:
            size = f"{observation['grams']:g}g"
        sizes.setdefault(
            size,
            {
                "label": size,
                "source": f"{observation['file']}, {observation['sheet']} row {observation['row']}",
            },
        )
    # Grouping names also contain explicit 150 g / 200 g mushroom pack labels.
    for item in items:
        for grams in item["observed_grams"]:
            size = f"{grams:g}g"
            sizes.setdefault(
                size, {"label": size, "source": f"Grouping/order reference row {item['row']}"}
            )
    candidates = []
    for size, observation in sorted(sizes.items()):
        weight = label_weight(size)
        evidence = {
            "weight_source": "ORDER_LABEL" if weight else "UNKNOWN",
            "volume_source": ("PROXY" if proxy else "RESEARCH")
            if selected and weight
            else "UNKNOWN",
            "estimate_kind": ("PROXY" if proxy else "RESEARCH") if selected and weight else None,
            "source_url": selected["url"] if selected else None,
            "source_note": (selected["label"] + ". " + selected["caveat"])
            if selected
            else "Occupied pack volume still needs a measurement or supplier specification.",
            "weight_note": observation["source"],
            "reference_id": selected["id"] if selected else None,
            "research_rows": sorted(row_ids),
            "research_version": data["version"],
            "estimate_low_cm3": round(float(weight) * 1000 / selected["high"], 2)
            if weight and selected
            else None,
            "estimate_high_cm3": round(float(weight) * 1000 / selected["low"], 2)
            if weight and selected
            else None,
        }
        candidates.append(
            {
                "pack_size": size,
                "aliases": [],
                "net_weight_kg": float(weight) if weight else None,
                "volume_cm3": None,
                "reviewed": False,
                "evidence": evidence,
            }
        )
    from app.modules.packing.profiles import lookup_pack_key

    existing = {p.pack_key for p in product.packing_profiles}
    existing.update(lookup_pack_key(a) for p in product.packing_profiles for a in p.aliases or [])
    return {
        "version": data["version"],
        "rows": items,
        "profiles": [c for c in candidates if c["pack_size"] not in existing],
        "guidance_profiles": candidates,
        "density_conflict": len(density_ids) > 1,
    }


def pending_drafts(products):
    result = []
    for product in products:
        existing = {p.pack_key for p in product.packing_profiles}
        # Aliases are owned by the existing profile; staging must respect them too.
        from app.modules.packing.profiles import lookup_pack_key

        existing.update(
            lookup_pack_key(alias) for p in product.packing_profiles for alias in p.aliases or []
        )
        for candidate in research_for(product)["profiles"]:
            if candidate["pack_size"] not in existing:
                result.append((product, candidate))
    return result


def staging_revision(products, pending):
    state = {
        "source": research_data()["version"],
        "products": [(p.id, p.packing_revision) for p in products],
        "profiles": [(p.id, c) for p, c in pending],
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def stage_drafts(db, pending):
    affected = defaultdict(int)
    for product, candidate in pending:
        db.add(
            ProductPackProfile(
                product=product, pack_key=candidate["pack_size"], revision=1, **candidate
            )
        )
        affected[product.id] += 1
    for product, _ in pending:
        if product.id in affected:
            product.packing_revision += 1
            affected.pop(product.id)
    db.flush()
