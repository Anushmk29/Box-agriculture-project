"""Read the supplied workbook into review-only reference data; never write to the DB."""

import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]


def build(source=None, destination=None):
    source = source or Path.home() / "Downloads" / "Rev.1 Grouping List (1).xlsx"
    destination = destination or ROOT / "backend" / "app" / "assets" / "packing_grouping.json"
    workbook = load_workbook(source, read_only=True, data_only=True)
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
                "notes": row[2],
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
                "special": bool(row[1]),
                "conflicts": [
                    key
                    for key, pair in zip(
                        ["box_material", "control_status", "weight_class"], flags, strict=True
                    )
                    if pair == (True, True)
                ],
                "source_flags": list(row[4:10]),
            }
        )
    workbook.close()
    destination.write_text(
        json.dumps(
            {
                "source": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sheet": "Sheet1",
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Prepared {len(entries)} reference rows for review; no product data changed")


if __name__ == "__main__":
    build()
