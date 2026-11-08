"""Workbook suggestions are review inputs, never executable packing rules."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def normalize_name(value):
    value = unicodedata.normalize("NFKC", value or "").replace("**", "").strip()
    suffix = (
        r"(?:BB|TT|NN|AA|KK|N)\s*$" if re.search(r"[\u0e00-\u0e7f]", value) else r"(?:BB|TT|NN)\s*$"
    )
    value = re.sub(r"\s+", " ", re.sub(suffix, "", value)).strip().casefold()
    return {"กะเพรา": "กระเพรา", "กระเพา": "กระเพรา"}.get(value, value)


@lru_cache
def reference():
    return json.loads(
        (Path(__file__).resolve().parents[2] / "assets" / "packing_grouping.json").read_text(
            encoding="utf-8"
        )
    )


def source_matches(product):
    selected = (product.packing_source or {}).get("rows", [])
    names = {normalize_name(getattr(product, field, None)) for field in ["name_en", "name_th", "name_vi"]} - {""}
    return [
        row
        for row in reference()["entries"]
        if row["row"] in selected or normalize_name(row["name"]) in names
    ]


def suggestions(product):
    names = " ".join(
        getattr(product, field) or "" for field in ["name_en", "name_th", "name_vi"]
    ).casefold()
    # These are deliberately suggestions. Ripeness, packaging and condition can
    # change firmness, so no name-based result is marked reviewed automatically.
    choices = [
        (r"basil|coriander|mint|dill|กระเพรา|กะเพรา|โหระพา|ผักชี|สะระแหน่", "HERB", "SOFT"),
        (r"leaf|leaves|spinach|lettuce|morning glory|ผักบุ้ง|ผักโขม|ผักกาด|ใบ", "LEAFY", "SOFT"),
        (r"mushroom|เห็ด", "VEGETABLE", "SOFT"),
        (r"galangal|ginger|potato|carrot|turmeric|ข่า|ขิง|มันเทศ|แครอท|ขมิ้น", "ROOT", "HARD"),
        (
            r"chilli|pepper|cucumber|aubergine|eggplant|egg plant|okra|พริก|แตงกวา|มะเขือ|กระเจี๊ยบ",
            "VEGETABLE",
            "SEMI_HARD",
        ),
    ]
    for pattern, category, firmness in choices:
        if re.search(pattern, names):
            return {"produce_type": category, "firmness": firmness, "reviewed": False}
    return {"produce_type": None, "firmness": None, "reviewed": False}
