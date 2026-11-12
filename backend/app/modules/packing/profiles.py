"""One source of truth for reviewed measurements, capacity and plan freshness."""

from __future__ import annotations

import math
from decimal import Decimal

from app.core.config import settings
from app.core.packs import lookup_pack_key as lookup_pack_key
from app.core.packs import pack_key as pack_key
from app.modules.packing.catalogue import dimension_source, external_volume_cm3, reference_for

PACKING_MODES = {"EL", "NON_EL"}
LAYER_ORDER = {"HARD": 0, "SEMI_HARD": 1, "SOFT": 2}


def profile_for(product, pack_size: str, *, strict: bool | None = None):
    legacy_weight_lookup = strict is False
    strict = settings.PACKING_PROFILES_ENABLED if strict is None else strict
    if (
        not legacy_weight_lookup
        and product.packing_volume_reviewed
        and product.packing_volume_l_per_kg
    ):
        if strict:
            require_product_settings(product)
        # Exact legacy profiles are unnecessary when order weights drive volume.
        return None
    try:
        key = lookup_pack_key(pack_size)
    except ValueError:
        if strict:
            raise ValueError(
                f"{product.t_code}: pack size '{pack_size}' needs an explicit unit"
            ) from None
        return None
    matches = [
        p
        for p in product.packing_profiles
        if key in {p.pack_key, *(lookup_pack_key(alias) for alias in (p.aliases or []))}
    ]
    if len(matches) > 1:
        raise ValueError(
            f"{product.t_code} {pack_size}: ambiguous pack aliases; correct Packing setup"
        )
    profile = matches[0] if matches else None
    if strict:
        if (
            not product.is_active
            or not product.packing_reviewed
            or product.firmness not in LAYER_ORDER
        ):
            raise ValueError(
                f"{product.t_code}: review classifications and firmness in Packing setup"
            )
        if not profile or not profile.reviewed:
            raise ValueError(
                f"{product.t_code} {pack_size}: enter and review pack weight and volume in Packing setup"
            )
    if profile and (
        any(
            v is not None and (not math.isfinite(float(v)) or float(v) <= 0)
            for v in [profile.volume_cm3, profile.net_weight_kg]
        )
        or (profile.reviewed and (profile.volume_cm3 is None or profile.net_weight_kg is None))
    ):
        raise ValueError(
            f"{product.t_code} {pack_size}: pack weight and volume must be positive and finite"
        )
    return profile


def require_product_settings(product):
    table = (product.packing_source or {}).get("table", {})
    unresolved = table.get("unresolved", [])
    if (
        not product.is_active
        or not product.packing_reviewed
        or product.firmness not in LAYER_ORDER
        or unresolved
    ):
        raise ValueError(f"{product.t_code}: review product settings in Packing setup")


def require_known_el(product, mode):
    if mode == "EL" and "control_status" in (product.packing_source or {}).get("table", {}).get(
        "unresolved", []
    ):
        raise ValueError(
            f"{product.t_code}: resolve the product EL classification in Packing setup"
        )


def unit_volume(product, pack_size: str, unit_weight_kg: float | None = None) -> float:
    from app.modules.packing.history import history_basis
    if history_basis(product):
        raise ValueError(f"{product.t_code}: these contents need an exact reviewed historical template; no occupied volume has been measured")
    if product.packing_volume_reviewed and product.packing_volume_l_per_kg is not None:
        value = float(product.packing_volume_l_per_kg)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{product.t_code}: occupied volume must be positive and finite")
        if unit_weight_kg is None or not math.isfinite(unit_weight_kg) or unit_weight_kg <= 0:
            raise ValueError(f"{product.t_code}: review the order weight before packing")
        if settings.PACKING_PROFILES_ENABLED:
            require_product_settings(product)
        return float(Decimal(str(value)) * Decimal(str(unit_weight_kg)) * 1000)
    if settings.PACKING_PROFILES_ENABLED and (product.packing_source or {}).get("table"):
        raise ValueError(f"{product.t_code}: confirm litres per kg in Packing setup")
    profile = profile_for(product, pack_size)
    return float(
        profile.volume_cm3 if profile and profile.reviewed else product.default_unit_volume_cm3
    )


def box_volume(catalogue) -> float:
    if settings.PACKING_PROFILES_ENABLED and not catalogue.packing_reviewed:
        raise ValueError(f"{catalogue.size_name}: review safe capacity in Packing setup")
    return external_volume_cm3(catalogue)


def product_limit(product, catalogue_id: str) -> float | None:
    limits = [
        float(limit.max_net_weight_kg)
        for limit in product.box_limits
        if limit.box_catalogue_id == catalogue_id and limit.reviewed
    ]
    return min(limits) if limits else None


def content_rows(box):
    return [
        (item.product, item.pack_size, item.final_packed_count, float(item.final_net_weight_kg))
        for item in box.items
    ]


def snapshot_for(box, rows=None, *, catalogue=None, history_evidence=None) -> dict:
    from sqlalchemy.orm import object_session

    from app.modules.packing.history import fit_evidence, history_basis, validate_rules
    from app.modules.packing.materials import material_policy
    catalogue = catalogue or box.override_box_type or box.box_type
    rows = content_rows(box) if rows is None else rows
    requires_history = any(history_basis(p) for p, *_ in rows)
    if history_evidence is None and (requires_history or (box.packing_snapshot or {}).get("history_evidence")):
        history_evidence = fit_evidence(object_session(box), box, rows, catalogue)
        if history_evidence is None and requires_history:
            raise ValueError("Historical fit guidance changed or does not cover these contents; review the packing plan")
    if history_evidence:
        validate_rules(rows, catalogue, box.packing_mode, order=box.order)
    inputs = {}
    for product, size, count, net in rows:
        profile = None if history_evidence else profile_for(product, size)
        key = lookup_pack_key(size) if settings.PACKING_PROFILES_ENABLED else size
        inputs[f"{product.id}:{key}"] = {
            "product_id": product.id,
            "material_policy": material_policy(product, box.order),
            "pack_size": size,
            "product_revision": product.packing_revision,
            "profile_id": profile.id if profile else None,
            "profile_weight_kg": float(profile.net_weight_kg)
            if profile and profile.net_weight_kg is not None
            else None,
            "evidence": (
                {"volume_source": "HISTORICAL_VERIFIED", "occupied_volume_known": False}
                if history_evidence else
                {
                    "volume_source": "PROXY" if product.packing_volume_estimated else "MANUAL",
                    "litres_per_kg": float(product.packing_volume_l_per_kg),
                }
                if product.packing_volume_reviewed and product.packing_volume_l_per_kg
                else profile.evidence
                if profile
                else None
            ),
            "profile_revision": profile.revision if profile else None,
            "unit_volume_cm3": None if history_evidence else unit_volume(product, size, net / count if count else None),
            "firmness": product.firmness,
            "control_status": None
            if "control_status"
            in (product.packing_source or {}).get("table", {}).get("unresolved", [])
            else product.control_status.value,
            "temperature": product.temperature.value,
            "box_material": product.box_material.value,
            "produce_type": product.produce_type.value,
            "weight_class": product.weight_class.value,
            "botanical_family": product.botanical_family,
        }
    result = {
        "mode": box.packing_mode,
        "catalogue_id": catalogue.id,
        "catalogue_revision": catalogue.packing_revision,
        "catalogue_name": catalogue.size_name,
        "catalogue_material": catalogue.box_type.value,
        "external_volume_cm3": box_volume(catalogue),
        "dimension_basis": "EXTERNAL",
        "dimension_source": dimension_source(catalogue),
        "safe_net_capacity_kg": float(catalogue.max_safe_net_weight_kg),
        "items": inputs,
    }
    if history_evidence:
        result["history_evidence"] = history_evidence
    initial = (box.packing_snapshot or {}).get("initial_plan")
    result["initial_plan"] = initial or {
        "catalogue_id": catalogue.id,
        "items": [{"product_id": p.id, "pack_size": size, "pack_count": count, "net_weight_kg": net}
                  for p, size, count, net in rows],
    }
    return result


def stale_reasons(box) -> list[str]:
    if box.is_locked:
        return []
    if not reference_for(box.override_box_type or box.box_type) or any(
        value is not None
        for value in [box.custom_length_cm, box.custom_width_cm, box.custom_height_cm]
    ):
        return ["Select a box size from the dimension workbook"]
    if (not settings.PACKING_PROFILES_ENABLED and not (box.packing_snapshot or {}).get("history_evidence")
            and not any(item.product.foam_below_kg is not None for item in box.items)):
        return []
    if box.packing_mode not in PACKING_MODES:
        return ["Select EL or Non-EL packing rules"]
    if not box.packing_snapshot:
        return ["Review a new plan using verified packing profiles"]
    try:
        current = snapshot_for(box)
    except ValueError as exc:
        return [str(exc)]
    return (
        []
        if current == box.packing_snapshot
        else ["Packing data changed; preview and apply a reviewed replan"]
    )


def require_current(box) -> None:
    reasons = stale_reasons(box)
    if reasons:
        raise ValueError(f"Box {box.box_number}: {'; '.join(reasons)}")


def packing_details(box) -> dict:
    saved = box.packing_snapshot or {}
    inputs = list(saved.get("items", {}).values())
    layers, volume = [], 0.0
    volume_known = True
    for item in box.items:
        source = next(
            (
                row
                for row in inputs
                if row["product_id"] == item.product_id and row["pack_size"] == item.pack_size
            ),
            {},
        )
        unit = source.get("unit_volume_cm3", float(item.product.default_unit_volume_cm3 or 0))
        if unit is None:
            volume_known = False
        else:
            volume += item.final_packed_count * unit
        firmness = source.get("firmness", item.product.firmness)
        layers.append(
            {
                "item_id": item.id,
                "product_code": item.product.t_code,
                "product_name": item.product.name_en,
                "pack_size": item.pack_size,
                "packed_count": item.final_packed_count,
                "firmness": firmness,
                "layer": {"HARD": "Bottom", "SEMI_HARD": "Middle", "SOFT": "Top"}.get(
                    firmness, "Needs review"
                ),
                "volume_source": (source.get("evidence") or {}).get("volume_source", "MEASURED"),
                "unit_weight": float(item.final_net_weight_kg) / max(1, item.final_packed_count),
            }
        )
    layers.sort(
        key=lambda row: (
            LAYER_ORDER.get(row["firmness"], 3),
            -row["unit_weight"],
            row["product_code"],
            row["pack_size"],
        )
    )
    for row in layers:
        row.pop("unit_weight")
    if box.is_locked:
        # Keep previously signed packing snapshots readable without rewriting them.
        volume_capacity = saved.get("external_volume_cm3", saved.get("internal_volume_cm3", 0))
    else:
        catalogue = box.override_box_type or box.box_type
        volume_capacity = external_volume_cm3(catalogue) if reference_for(catalogue) else 0
    usable = volume_capacity * 0.92
    return {
        "packing_mode": box.packing_mode,
        "needs_replan": bool(stale_reasons(box)),
        "replan_reasons": stale_reasons(box),
        "history_evidence": saved.get("history_evidence"),
        "volume_cm3": round(volume, 2) if volume_known else None,
        "usable_volume_cm3": round(usable, 2),
        "volume_utilization_pct": round(volume / usable * 100, 2) if usable and volume_known else None,
        "layers": layers,
    }
