"""Simplified settings and conservative, repeatable workbook reconciliation."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.models.master_data import (
    BoxMaterial,
    ControlStatus,
    ProductBoxLimit,
    TemperatureZone,
    WeightClass,
)
from app.modules.packing.catalogue import reference_for
from app.modules.packing.materials import material_for_temperature
from app.modules.packing.reference import normalize_name, reference, suggestions
from app.modules.packing.research import research_for

FIELDS = ("box_material", "temperature", "control_status", "weight_class")
ENUMS = {
    "box_material": BoxMaterial,
    "temperature": TemperatureZone,
    "control_status": ControlStatus,
    "weight_class": WeightClass,
}


@lru_cache
def sources():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "assets/product_table_sources.json").read_text(
            encoding="utf-8"
        )
    )


def matching_rows(product, code_rows):
    names = {normalize_name(getattr(product, f) or "") for f in ["name_th", "name_en", "name_vi"]}
    names.update(normalize_name(r[k]) for r in code_rows for k in ["name_th", "name_en"])
    return [r for r in reference()["entries"] if normalize_name(r["name"]) in names - {""}]


def parse_limits(notes, boxes):
    """Only explicit kg notes and uniquely named approved boxes are executable."""
    limits, issues = {}, []
    pattern = r"(โฟม|ฟ\.|ก\.)\s*([^\d,]+?)\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(กก|ลูก|หวี)"
    matches = list(re.finditer(pattern, notes or ""))
    for match in matches:
        prefix, name, kg, high, unit = match.groups()
        material = "CARDBOARD" if prefix == "ก." else "FOAM"
        candidates = [
            b
            for b in boxes
            if (r := reference_for(b))
            and r["box_type"] == material
            and r["name_th"].strip() == name.strip()
        ]
        if high is not None:
            issues.append({"note": match.group(), "max_net_weight_kg": None,
                           "range_kg": [float(kg), float(high)] if unit == "กก" else None,
                           "unit": unit, "reason": "Confirm the meaning of this range before entering a limit"})
            continue
        if unit != "กก" or len(candidates) != 1:
            issues.append(
                {"note": match.group(), "max_net_weight_kg": float(kg) if unit == "กก" else None}
            )
            continue
        box = candidates[0]
        # Never silently clamp source data to catalogue capacity: both limits apply.
        limits[box.id] = min(float(kg), limits.get(box.id, float("inf")))
    if notes and not matches:
        issues.append({"note": notes, "max_net_weight_kg": None})
    return limits, issues


def reconciliation(product, boxes):
    old = (product.packing_source or {}).get("table", {})
    manual = set(old.get("manual_fields", []))
    if not old and product.packing_reviewed:
        manual.update(FIELDS)
    code_rows = [
        r for r in sources()["products"] if r["code"].casefold() == product.t_code.casefold()
    ]
    matches = matching_rows(product, code_rows)
    prior_rows = (product.packing_source or {}).get("rows", [])
    selected = old.get("grouping_row") or (prior_rows[0] if len(prior_rows) == 1 else None)
    used = [r for r in matches if r["row"] == selected] if selected else matches
    fields, unresolved = {}, []
    if len(code_rows) > 1 and "identity" not in manual:
        unresolved.append("identity")
    for key in FIELDS:
        values = {r[key] for r in used if r[key] is not None}
        value = next(iter(values)) if len(values) == 1 else None
        conflict = any(key in r.get("conflicts", []) for r in used)
        if key == "control_status" and not values and not conflict:
            marked = {r[key] for r in code_rows if r[key]}
            value = next(iter(marked)) if len(marked) == 1 else None
        if conflict:
            value = None
        if key in manual:
            value = ENUMS[key](getattr(product, key)).value
        fields[key] = value
        if value is None:
            unresolved.append(key)
    if fields["temperature"] is not None:
        fields["box_material"] = material_for_temperature(fields["temperature"]).value
        if "box_material" in unresolved:
            unresolved.remove("box_material")
    notes = list(dict.fromkeys(r["notes"] for r in used if r["notes"]))
    limit_values, limit_issues = {}, []
    for note in notes:
        values, issues = parse_limits(note, boxes)
        for key, value in values.items():
            limit_values[key] = min(value, limit_values.get(key, float("inf")))
        limit_issues.extend(issues)
    if limit_issues and ("limits" not in manual or "limits" in old.get("unresolved", [])):
        unresolved.append("limits")
    return {
        "fields": fields,
        "planning_basis": old.get("planning_basis", "VOLUME"),
        "unresolved": sorted(unresolved),
        "manual_fields": sorted(manual),
        "grouping_row": selected,
        "grouping_rows": [r["row"] for r in matches],
        "code_rows": code_rows,
        "notes": notes,
        "limit_issues": limit_issues,
        "limits": limit_values,
        "sources": sources()["sources"],
        **({"limit_resolutions": old["limit_resolutions"]} if old.get("limit_resolutions") else {}),
        **({"limits_guidance_only": True} if old.get("limits_guidance_only") else {}),
    }


def apply_reconciliation(db, product, boxes):
    proposal = reconciliation(product, boxes)
    old_source = dict(product.packing_source or {})
    old_table = old_source.get("table", {})
    manual = set(proposal["manual_fields"])
    classifications_changed = False
    for key, value in proposal["fields"].items():
        if value and (key not in manual or key == "box_material" and proposal["fields"]["temperature"] is not None):
            resolved = ENUMS[key](value)
            classifications_changed |= getattr(product, key) != resolved
            setattr(product, key, resolved)
    rows = proposal["code_rows"]
    if len(rows) == 1 and "identity" not in manual:
        product.name_en = rows[0]["name_en"] or product.name_en
        product.name_th = rows[0]["name_th"] or product.name_th
    suggestion = suggestions(product)
    if "firmness" not in manual and not product.firmness and suggestion["firmness"]:
        product.firmness = suggestion["firmness"]
    volume_research = old_source.get("volume_web_research", {})
    if (
        product.packing_volume_l_per_kg is None
        and "volume" not in manual
        and volume_research.get("status") != "WITHDRAWN_MISMATCH"
    ):
        estimates = []
        for p in product.packing_profiles:
            e = p.evidence or {}
            volume = e.get("estimate_high_cm3") or p.volume_cm3
            if volume and p.net_weight_kg:
                estimates.append(float(volume) / float(p.net_weight_kg) / 1000)
        if not estimates:
            for p in research_for(
                product, source_rows=proposal["grouping_rows"], additional_sizes=["1kg"]
            )["guidance_profiles"]:
                e = p["evidence"]
                if e.get("estimate_high_cm3") and p["net_weight_kg"]:
                    estimates.append(e["estimate_high_cm3"] / p["net_weight_kg"] / 1000)
        if estimates:
            product.packing_volume_l_per_kg = round(max(estimates), 6)
            product.packing_volume_estimated = True
            product.packing_volume_reviewed = False
    if "limits" not in manual:
        existing = {limit.box_catalogue_id: limit for limit in product.box_limits}
        for identity, limit in existing.items():
            if identity not in proposal["limits"] and not limit.reviewed:
                product.box_limits.remove(limit)
                db.delete(limit)
        for identity, value in proposal["limits"].items():
            if identity not in existing:
                limit = ProductBoxLimit(
                    product=product,
                    box_catalogue_id=identity,
                    max_net_weight_kg=value,
                    reviewed=False,
                    source_note="; ".join(proposal["notes"])[:2048],
                )
                db.add(limit)
            elif not existing[identity].reviewed:
                existing[identity].max_net_weight_kg = value
    # A no-op reimport preserves revision and approvals.
    old_source["table"] = proposal
    if old_table != proposal or classifications_changed:
        product.packing_reviewed = False
        product.packing_volume_reviewed = False
        product.packing_revision += 1
    product.packing_source = old_source
    return proposal


def table_problems(product):
    table = (product.packing_source or {}).get("table", {})
    problems = list(table.get("unresolved", []))
    if "temperature" not in problems and product.box_material != material_for_temperature(product.temperature):
        problems.append("box_material")
    if not product.firmness:
        problems.append("firmness")
    if not product.packing_volume_l_per_kg and table.get("planning_basis", "VOLUME") != "HISTORY":
        problems.append("volume")
    if not product.packing_reviewed or (table.get("planning_basis", "VOLUME") != "HISTORY" and not product.packing_volume_reviewed):
        problems.append("confirmation")
    return list(dict.fromkeys(problems))


def row_out(product):
    table = (product.packing_source or {}).get("table", {})
    unresolved = table.get("unresolved", [])
    return {
        "id": product.id,
        "t_code": product.t_code,
        "name_en": product.name_en,
        "name_th": product.name_th,
        "name_vi": product.name_vi,
        "planning_basis": table.get("planning_basis", "VOLUME"),
        "packing_revision": product.packing_revision,
        "foam_below_kg": float(product.foam_below_kg) if product.foam_below_kg is not None else None,
        **{
            key: None if key in unresolved else ENUMS[key](getattr(product, key)).value
            for key in FIELDS
        },
        "produce_type": getattr(product.produce_type, "value", product.produce_type),
        "firmness": product.firmness,
        "volume_l_per_kg": float(product.packing_volume_l_per_kg)
        if product.packing_volume_l_per_kg is not None
        else None,
        "volume_estimated": product.packing_volume_estimated,
        "ready": not table_problems(product),
        "problems": table_problems(product),
        "source_choices": [
            r for r in reference()["entries"] if r["row"] in table.get("grouping_rows", [])
        ],
        "grouping_row": table.get("grouping_row"),
        "temperature_source": "UNRESOLVED" if "temperature" in unresolved else (
            "STAFF" if "temperature" in table.get("manual_fields", []) else (
                "WORKBOOK" if table.get("grouping_rows") else "SAVED"
            )
        ),
        "identity_choices": table.get("code_rows", []) if "identity" in unresolved else [],
        "limit_issues": table.get("limit_issues", []) if "limits" in unresolved else [],
        "limits": [
            {
                "box_catalogue_id": limit.box_catalogue_id,
                "max_net_weight_kg": float(limit.max_net_weight_kg),
            }
            for limit in product.box_limits
        ],
    }
