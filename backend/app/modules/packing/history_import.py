"""Read-only extraction of the supplied packing corpus with per-page provenance.

PDF strings and workbook notes are data, never executable instructions.
PyMuPDF is imported only by the extraction CLI; runtime planning reads the asset.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

DIGITS = str.maketrans({chr(0x158 + number): str(number) for number in range(10)})
NUMBER = re.compile(r"^\d[\d,]*(?:\.\d+)?$")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def number(value):
    try:
        return float(Decimal(value.replace(",", ""))) if NUMBER.fullmatch(value) else None
    except InvalidOperation:
        return None


def _text(words):
    return " ".join(word[4] for word in sorted(words, key=lambda word: word[0])).strip()


def parse_label(page, *, source: str, page_number: int) -> dict:
    words = page.get_text("words")
    text = page.get_text(sort=True)
    header = re.search(r"BOX NO\s*(\d+)\s*(.*?)\s+Box Page\s*([^\n]+)", text)
    sub = re.search(r"Sub\s*:\s*([^\n]+)", text)
    delivery = re.search(r"Delivery\s*:\s*(\d{2}/\d{2}/\d{4})", text)
    customer = re.search(r"CUSTOMER\s*:\s*(.*?)(?:Delivery|$)", text, re.MULTILINE)
    if not header or not sub or not delivery:
        raise ValueError("Label needs box number, subcustomer and delivery date")
    parts = re.findall(r"\d+", header[3])
    headings = {word[4]: word for word in words if word[4] in {"No.", "Weight", "Size", "Pack"}}
    if len(headings) != 4 or len(parts) != 2:
        raise ValueError("Label table headings or page sequence are incomplete")
    left = headings["No."][0]
    columns = [headings["Weight"][0],
               (headings["Weight"][2] + headings["Size"][0]) / 2,
               (headings["Size"][2] + headings["Pack"][0]) / 2]
    anchors = sorted((word for word in words if abs(word[0] - left) < 3
                      and word[1] > headings["No."][1] + 10
                      and re.fullmatch(r"\d+", word[4])), key=lambda word: word[1])
    items, issues = [], []
    for anchor in anchors:
        row_words = [word for word in words if abs(word[1] - anchor[1]) < 2]
        description = _text([word for word in row_words if anchor[2] + 2 < word[0] < columns[0]])
        cells = [_text([word for word in row_words if low <= word[0] < high])
                 for low, high in zip(columns, [*columns[1:], float("inf")], strict=True)]
        weight, size, packs = number(cells[0]), cells[1], number(cells[2])
        code, _, description_names = description.partition(" ")
        thai, separator, english = description_names.partition("/")
        entry = {
            "line": int(anchor[4]), "source": source, "page": page_number,
            "raw_code": code, "code": code.translate(DIGITS).strip("."),
            "name_th": thai.strip() if re.search(r"[\u0e00-\u0e7f]", thai) else "",
            "name_en": english.strip() if separator else (thai.strip() if not re.search(r"[\u0e00-\u0e7f]", thai) else ""),
            "name_vi": "", "raw_weight": weight, "raw_pack_quantity": packs, "raw_cells": cells,
            "pack_size": size, "pack_count": None, "net_weight_kg": None,
            "quantity_kind": "UNKNOWN", "issues": [],
        }
        grams = re.fullmatch(r"(\d+(?:\.\d+)?)\s*G", size, re.IGNORECASE)
        if grams:
            entry["quantity_kind"] = "FIXED_PACK"
            if packs is not None and packs > 0 and packs.is_integer():
                entry["pack_count"] = int(packs)
            if weight is not None and weight > 0:
                entry["net_weight_kg"] = weight
            if packs is None or weight is None or abs(packs * float(grams[1]) / 1000 - weight) > .011:
                entry["issues"].append("Pack count × pack size differs from declared weight")
        elif size.upper() == "KG":
            entry["quantity_kind"] = "LOOSE_WEIGHT"
            entry["issues"].append("Loose kilograms need a confirmed physical pack count and pack format")
        elif size.upper() == "PIECES":
            entry["quantity_kind"] = "PIECES"
            entry["pack_count"] = int(packs) if packs is not None and packs > 0 and packs.is_integer() else None
            entry["issues"].append("Piece-based Weight column needs actual net kilograms confirmed")
        else:
            entry["issues"].append("Pack format needs review")
        if not code or not description_names or weight is None or packs is None:
            entry["issues"].append("Incomplete extracted item row")
        items.append(entry)
    # A continuation page can contain only the grand total.
    totals = []
    after = max((anchor[1] for anchor in anchors), default=headings["No."][1]) + 5
    for word in words:
        if columns[0] <= word[0] < columns[1] and word[1] > after and number(word[4]) is not None:
            totals.append(number(word[4]))
    return {
        "source": source, "page": page_number, "subcustomer": sub[1].strip(),
        "customer": customer[1].strip() if customer else "",
        "delivery_date": datetime.strptime(delivery[1], "%d/%m/%Y").date().isoformat(),
        "box_number": int(header[1]), "box_name": header[2].strip(),
        "part": int(parts[0]), "parts": int(parts[1]),
        "items": items, "declared_total_weight": totals[-1] if totals else None,
        "issues": issues,
    }


def parse_register(page, *, source: str, page_number: int) -> list[dict]:
    words = page.get_text("words")
    starts = sorted({round(word[0], 1) for word in words if word[4] == "BOX"})
    result = []
    for low, high in zip(starts, [*starts[1:], float("inf")], strict=True):
        column = sorted((w for w in words if low - 1 <= w[0] < high - 1), key=lambda w: w[1])
        lines = []
        for word in column:
            if not lines or abs(word[1] - lines[-1][0][1]) > 2:
                lines.append([word])
            else:
                lines[-1].append(word)
        current = None
        for line in lines:
            value = _text(line)
            match = re.match(r"BOX\s+(.+?)\s*/\s*(\d+)\s+(.+)", value)
            if match:
                tail = match[3]
                box_name = re.search(r"([กฟ]\..+)$", tail)
                current = {"subcustomer": match[1].strip(), "box_number": int(match[2]),
                           "box_name": box_name[1].strip() if box_name else "",
                           "source": source, "page": page_number, "items": []}
                result.append(current)
            elif value.startswith("BOX"):
                current = None
            elif current and re.match(r"^\d+\.", value):
                # The final register number is deliberately left uninterpreted.
                row = re.match(r"^\d+\.\s*(.*?)\s+(\d[\d,.]*)\s+(.*?)\s+(\d[\d,.]*)$", value)
                if row:
                    current["items"].append({"name": row[1], "raw_weight": number(row[2]),
                                              "pack_size": row[3], "trailing_value": number(row[4])})
                else:
                    current["items"].append({"unparsed": value})
    return result


def extract_corpus(root: Path) -> dict:
    import pymupdf

    documents, labels, registers, problems, seen = [], [], defaultdict(list), [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = {"file": relative, "sha256": checksum, "kind": "support", "pages": 0}
        documents.append(doc)
        if checksum in seen:
            previous = seen[checksum]
            doc.update(duplicate_of=previous["file"], kind=previous["kind"], pages=previous["pages"])
            continue
        seen[checksum] = doc
        if path.suffix.lower() != ".pdf":
            continue
        with pymupdf.open(path) as pdf:
            doc["pages"] = len(pdf)
            first = pdf[0].get_text()
            doc["kind"] = "labels" if "Packing Slip" in first else "register" if "BOX " in first else "summary"
            for index, page in enumerate(pdf, 1):
                try:
                    if doc["kind"] == "labels":
                        labels.append({**parse_label(page, source=relative, page_number=index),
                                       "shipment": path.parent.name, "source_sha256": checksum})
                    elif doc["kind"] == "register":
                        registers[path.parent.name].extend(parse_register(page, source=relative, page_number=index))
                except (ValueError, IndexError) as exc:
                    problems.append({"file": relative, "page": index, "issue": str(exc)})
    grouped = defaultdict(list)
    for label in labels:
        key = (label["source_sha256"], label["shipment"], label["delivery_date"], label["subcustomer"], label["box_number"])
        grouped[key].append(label)
    examples = []
    for pages in grouped.values():
        pages.sort(key=lambda page: page["part"])
        first = pages[0]
        issues = []
        if [page["part"] for page in pages] != list(range(1, first["parts"] + 1)) or any(page["parts"] != first["parts"] for page in pages):
            issues.append("Missing or repeated continuation pages; box identity needs review")
        if len({page["box_name"] for page in pages}) != 1:
            issues.append("Box type differs across continuation pages")
        items = [item for page in pages for item in page["items"]]
        if not items:
            issues.append("No item rows extracted")
        totals = [page["declared_total_weight"] for page in pages if page["declared_total_weight"] is not None]
        if totals and abs(sum(item["raw_weight"] or 0 for item in items) - totals[-1]) > .011:
            issues.append("Item Weight column does not reconcile to label total")
        candidates = [r for r in registers[first["shipment"]]
                      if r["subcustomer"].rstrip(".").casefold() == first["subcustomer"].rstrip(".").casefold()
                      and r["box_number"] == first["box_number"]]
        register = candidates[0] if len(candidates) == 1 else None
        if not register:
            issues.append("Matching box register needs reconciliation")
        elif (register["box_name"] != first["box_name"] or len(register["items"]) != len(items)
              or any(r.get("raw_weight") != item["raw_weight"] or r.get("pack_size") != item["pack_size"]
                     for r, item in zip(register["items"], items, strict=False))):
            issues.append("Register box type, item count, weights or pack labels differ")
        date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", first["shipment"])
        if date_match and f"20{date_match[3]}-{date_match[2]}-{date_match[1]}" != first["delivery_date"]:
            issues.append("Folder date differs from label delivery date")
        example = {key: first[key] for key in ("shipment", "delivery_date", "subcustomer", "customer", "box_number", "box_name", "source_sha256")}
        example.update(items=items, pages=[page["page"] for page in pages], source=first["source"],
                       register={key: register[key] for key in ("source", "page")} if register else None,
                       declared_total_weight=totals[-1] if totals else None, issues=issues)
        example["id"] = digest(example)
        examples.append(example)
    body = {"schema_version": 1, "documents": documents, "examples": examples, "extraction_issues": problems}
    return {**body, "version": digest(body), "summary": {
        "label_pages": len(labels), "examples": len(examples),
        "items": sum(len(example["items"]) for example in examples),
        "shipment_sets": len({example["shipment"] for example in examples}),
        "examples_needing_source_review": sum(bool(example["issues"] or any(item["issues"] for item in example["items"])) for example in examples),
        "extraction_issues": len(problems),
    }}
