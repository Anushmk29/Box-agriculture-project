"""Approved external box sizes from the supplied dimension workbook."""

import json
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def dimension_reference():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "assets/box_dimensions.json").read_text(
            encoding="utf-8"
        )
    )


def reference_for(catalogue):
    material = getattr(catalogue.box_type, "value", catalogue.box_type)
    try:
        dimensions = tuple(
            Decimal(str(getattr(catalogue, f"{axis}_cm"))) for axis in ["length", "width", "height"]
        )
    except (InvalidOperation, TypeError, AttributeError):
        return None
    return next(
        (
            row
            for row in dimension_reference()["rows"]
            if row["box_type"] == material
            and dimensions
            == tuple(Decimal(str(row[f"{axis}_cm"])) for axis in ["length", "width", "height"])
        ),
        None,
    )


def require_workbook_box(catalogue):
    reference = reference_for(catalogue)
    if not reference:
        raise ValueError(
            "Select a box size listed in 100726 Dimention (1).xlsx; custom dimensions are not supported"
        )
    return reference


def external_volume_cm3(catalogue):
    row = require_workbook_box(catalogue)
    return float(
        Decimal(str(row["length_cm"]))
        * Decimal(str(row["width_cm"]))
        * Decimal(str(row["height_cm"]))
    )


def dimension_source(catalogue):
    row = reference_for(catalogue)
    if not row:
        return None
    source = dimension_reference()
    return {
        "file": source["source_file"],
        "sha256": source["source_sha256"],
        "sheet": source["sheet"],
        "row": row["row"],
        "basis": "EXTERNAL",
    }
