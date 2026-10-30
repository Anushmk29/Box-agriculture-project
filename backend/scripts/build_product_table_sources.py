"""Extract source values without interpreting document text as instructions."""

import hashlib
import json
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def build():
    codes = ROOT / "documents/new docs-mail/Product Codes/PRODUCT CODES.xlsx"
    grouping = Path.home() / "Downloads/Rev.1 Grouping List (1).xlsx"
    result = {"sources": [], "products": []}
    for source in [codes, grouping]:
        result["sources"].append(
            {"file": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        )
    workbook = load_workbook(codes, read_only=True, data_only=True)
    for number, row in enumerate(workbook.active.iter_rows(values_only=True), 1):
        code = str(row[2] or "").strip()
        if number < 8 or not code:
            continue
        result["products"].append(
            {
                "row": number,
                "code": code,
                "name_th": str(row[3] or "").strip(),
                "name_en": str(row[4] or "").strip(),
                "control_status": "EL" if str(row[5] or "").strip().upper() == "EL" else None,
            }
        )
    workbook.close()
    workbook = load_workbook(grouping, read_only=True, data_only=True)
    entries = []
    for number, row in enumerate(workbook.active.iter_rows(values_only=True), 1):
        if number < 4 or not (row[3] or row[1]):
            continue
        flags = [
            (str(row[a] or "").strip() == "/", str(row[b] or "").strip() == "/")
            for a, b in [(4, 5), (6, 7), (8, 9)]
        ]
        material = (
            "FOAM"
            if flags[0] == (True, False)
            else "CARDBOARD"
            if flags[0] == (False, True)
            else None
        )
        entries.append(
            {
                "row": number,
                "name": str(row[3] or row[1]).strip(),
                "notes": str(row[2]).strip() if row[2] else None,
                "box_material": material,
                "temperature": "COLD" if material == "FOAM" else "NORMAL" if material else None,
                "control_status": "EL"
                if flags[1] == (True, False)
                else "COMBINABLE"
                if flags[1] == (False, True)
                else None,
                "weight_class": "LIGHT"
                if flags[2] == (True, False)
                else "HEAVY"
                if flags[2] == (False, True)
                else None,
                "conflicts": [
                    key
                    for key, flags_pair in zip(
                        ["box_material", "control_status", "weight_class"], flags, strict=True
                    )
                    if flags_pair == (True, True)
                ],
                "special": bool(row[1]),
                "source_flags": list(row[4:10]),
            }
        )
    workbook.close()
    assets = ROOT / "backend/app/assets"
    (assets / "product_table_sources.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (assets / "packing_grouping.json").write_text(
        json.dumps(
            {
                "source": grouping.name,
                "sha256": result["sources"][1]["sha256"],
                "sheet": "Sheet1",
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "product_rows": len(result["products"]),
                "grouping_rows": len(entries),
                "sources": result["sources"],
            }
        )
    )


if __name__ == "__main__":
    build()
