"""Bounded, database-free extraction using versioned customer mappings."""

import json
import re
from collections import defaultdict
from pathlib import Path

import openpyxl
import xlrd

from app.core import work_limits as limits
from app.core.document_limits import inspect_document, validate_drafts
from app.core.work_limits import WorkLimit
from app.modules.orders.quantities import normalize_pack, source_amount, unit_key
from app.services.canonical_schema import CanonicalDraftItem, CanonicalOrderDraft, IngestionChannel


def references():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "assets/excel_templates.json").read_text(
            encoding="utf-8"
        )
    )


def is_reference_definition(definition):
    """Only an unchanged bundled mapping can be applied without setup."""
    return any(definition == reference for reference in references())


def normalized(value):
    return " ".join(str(value or "").casefold().split())


def load_sheets(path):
    inspect_document(path)
    result = []
    if Path(path).suffix.lower() == ".xls":
        wb = xlrd.open_workbook(path)
        for sheet in wb.sheets():
            if (
                sheet.nrows > limits.MAX_ROWS
                or sheet.ncols > limits.MAX_COLUMNS
                or sheet.nrows * sheet.ncols > limits.MAX_CELLS
            ):
                raise WorkLimit("Worksheet exceeds the safe cell budget")
            result.append((sheet.name, [sheet.row_values(i) for i in range(sheet.nrows)], {}))
    else:
        # Read formulas separately so uncached order formulas cannot disappear as blanks.
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            for sheet in wb:
                if (sheet.max_row or 0) * (sheet.max_column or 0) > limits.MAX_CELLS:
                    raise WorkLimit("Worksheet exceeds the safe cell budget")
                missing = {
                    (cell.row, cell.column): str(cell.value)
                    for row in formulas[sheet.title]
                    for cell in row
                    if cell.data_type == "f"
                }
                result.append((sheet.title, list(sheet.iter_rows(values_only=True)), missing))
        finally:
            wb.close()
            formulas.close()
    return result


def cell(row, column):
    return row[column - 1] if column and column <= len(row) else None


def locate(sheets, definition):
    matches = []
    for spec in definition["sheets"]:
        found = []
        for name, rows, formulas in sheets:
            for index, row in enumerate(rows[:80]):
                if spec["anchors"] and all(
                    normalized(cell(row, int(c))) == normalized(v)
                    for c, v in spec["anchors"].items()
                ):
                    found.append((name, rows, formulas, index, spec))
                    break
        if len(found) != 1:
            return None
        matches.extend(found)
    return matches


def identity_score(filename, located, definition):
    # Only identity/header area is inspected, never product descriptions.
    header = " ".join(
        normalized(v)
        for _, rows, _, index, _ in located
        for row in rows[:index]
        for v in row
        if v is not None
    )
    filename = re.sub(r"^[0-9a-fA-F-]{36}_", "", Path(filename).stem).casefold()
    tokens = definition.get("identity_tokens", [])

    def contains(text, token):
        return bool(re.search(r"(?<!\w)" + re.escape(normalized(token)) + r"(?!\w)", text))

    exact = any(
        normalized(v) == normalized(t)
        for _, rows, _, index, _ in located
        for row in rows[:index]
        for v in row
        for t in tokens
    )
    filename = filename.replace("_", " ")
    return (8 if exact else 4 if any(contains(header, t) for t in tokens) else 0) + (
        2 if any(contains(filename, t) for t in tokens) else 0
    )


def parse_profiles(path, profiles, *, original_name=None, selected_key=None):
    sheets = load_sheets(path)
    candidates = []
    for profile in profiles:
        definition = profile.get("definition", profile)
        located = locate(sheets, definition)
        if located:
            score = identity_score(original_name or path, located, definition)
            candidates.append((score, profile, located))
    best = max((c[0] for c in candidates), default=0)
    choices = [c for c in candidates if c[0] == best]
    explicit = next(
        (c for c in candidates if c[1].get("definition", c[1])["key"] == selected_key), None
    )
    issues = []
    if explicit:
        chosen = explicit
    elif len(choices) == 1 and best > 0:
        chosen = choices[0]
        # A conflicting filename must be explicitly resolved, even if the header wins.
        named = {
            p.get("definition", p)["key"]
            for p in profiles
            if any(
                re.search(
                    r"(?<!\w)" + re.escape(t.casefold()) + r"(?!\w)",
                    Path(original_name or path).stem.casefold().replace("_", " "),
                )
                for t in p.get("definition", p).get("identity_tokens", [])
            )
        }
        if named and chosen[1].get("definition", chosen[1])["key"] not in named:
            issues.append(
                "Filename and workbook customer identity conflict; select the correct template"
            )
    else:
        chosen = None
    if not chosen:
        return [
            CanonicalOrderDraft(
                source_file=Path(original_name or path).name,
                channel=IngestionChannel.EXCEL_TABULAR,
                client_code="REVIEW",
                metadata={
                    "template": {
                        "status": "REVIEW_REQUIRED",
                        "issues": [
                            "Select a customer template with matching columns, or configure a new version"
                        ],
                        "candidates": [c[1].get("definition", c[1])["key"] for c in candidates],
                    }
                },
            )
        ]
    _, profile, located = chosen
    definition = profile.get("definition", profile)
    metadata = {
        "id": profile.get("id"),
        "key": definition["key"],
        "version": profile.get("version", 1),
        "status": "MATCHED",
        "confirmed": profile.get("confirmed", False),
        "automatic": is_reference_definition(definition),
        "issues": issues,
        "default_unit": definition.get("default_unit"),
        "candidates": [c[1].get("definition", c[1])["key"] for c in candidates],
    }
    metadata["shipment_references"] = list(
        dict.fromkeys(
            " | ".join(str(v) for v in row if v is not None)[:2048]
            for _, rows, _, header, _ in located
            for row in rows[:header]
            if any(
                re.search(r"\b(date|arrival|shipment|lot)\b|วันที่.*ส่ง", str(v), re.I)
                for v in row
                if v is not None
            )
        )
    )[:20]
    grouped = defaultdict(list)
    for sheet_name, rows, formulas, header, spec in located:
        fields = spec["fields"]
        customers = spec.get("customers") or {
            str(fields.get("quantity", 1)): definition["client_code"]
        }
        for row_index, row in enumerate(rows[header + 1 :], header + 2):
            name = cell(row, fields["name"])
            if not name:
                name = next((cell(row, c) for c in fields.get("alt", []) if cell(row, c)), None)
            for quantity_column, customer in customers.items():
                quantity_column = int(quantity_column)
                raw = cell(row, quantity_column)
                formula = formulas.get((row_index, quantity_column))
                if (
                    not name
                    and not cell(row, fields.get("code"))
                    and formula
                    and re.match(r"=\s*(SUM|SUBTOTAL)\s*\(", formula, re.I)
                ):
                    continue  # Approved layouts' unlabelled subtotal rows.
                if raw is None or str(raw).strip() == "":
                    if not formula:
                        continue
                if raw is not None and str(raw).strip() in {"0", "0.0"}:
                    continue
                if normalized(name) in {"total", "รวม", "product name", "english name", "name eng"}:
                    continue
                problems = []
                raw_unit = cell(row, fields.get("unit"))
                if definition["kind"] == "text":
                    text = str(raw or "")
                    if re.search(r"\bSTOP\b", text, re.I):
                        amount, unit, action = 0, "unknown", "CANCEL"
                        product_text = re.sub(r"\bSTOP\b", "", text, flags=re.I).strip()
                    else:
                        match = re.search(
                            r"([+-]?\d+(?:[.,]\d+)?)\s*(kg|kgs|gms|g|pkts|packs|pcs)?\s*$",
                            text,
                            re.I,
                        )
                        product_text = text[: match.start()].strip() if match else text
                        raw_unit = match[2] if match else None
                        try:
                            amount, unit = (
                                source_amount(
                                    match[1],
                                    raw_unit or definition.get("default_unit") or "unknown",
                                )
                                if match
                                else (0, "unknown")
                            )
                            if not match:
                                problems.append("Enter the amount and unit from this text order")
                        except ValueError as exc:
                            amount, unit = 0, "unknown"
                            problems.append(str(exc))
                        action = "ORDER"
                    code_match = re.match(r"([A-Z0-9]+(?:-[A-Z0-9]+)?)\s+(.+)", product_text)
                    code = (
                        code_match[1]
                        if code_match and any(c.isdigit() for c in code_match[1])
                        else None
                    )
                    name = code_match[2] if code else product_text
                else:
                    action = "ORDER"
                    code = cell(row, fields.get("code"))
                    # Repeated headers and separators are not candidate order entries.
                    if not name and not code and not formula:
                        continue
                    try:
                        amount, unit = source_amount(
                            raw, str(raw_unit or definition.get("default_unit") or "unknown")
                        )
                    except ValueError as exc:
                        amount, unit = 0, "unknown"
                        problems.append(str(exc))
                if amount == 0 and not problems and action != "CANCEL":
                    continue
                raw_pack = cell(row, fields.get("pack"))
                if not raw_pack and fields.get("description_pack"):
                    description = str(cell(row, fields["description_pack"]) or "")
                    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(กรัม|grams?|g|kg)\b", description, re.I)
                    raw_pack = (
                        match[1] + ("g" if match[2] in {"กรัม", "gram", "grams"} else match[2])
                        if match
                        else None
                    )
                adjacent = cell(row, fields.get("adjacent"))
                try:
                    pack, pack_issues = normalize_pack(
                        raw_pack,
                        numeric_unit=definition.get("numeric_pack_unit"),
                        adjacent_kg=adjacent if isinstance(adjacent, (float, int)) else None,
                    )
                    problems.extend(pack_issues)
                except ValueError as exc:
                    pack = None
                    problems.append(str(exc))
                if formula and raw is None:
                    problems.append("Order formula has no saved result; enter the confirmed amount")
                if not name:
                    problems.append("Order entry has no product description")
                source = {
                    "file": Path(original_name or path).name,
                    "sheet": sheet_name,
                    "row": row_index,
                    "cells": {
                        k: f"{openpyxl.utils.get_column_letter(v)}{row_index}"
                        for k, v in fields.items()
                        if isinstance(v, int)
                    },
                    "quantity_cell": f"{openpyxl.utils.get_column_letter(quantity_column)}{row_index}",
                    "raw_quantity": raw,
                    "raw_unit": raw_unit,
                    "raw_pack_size": raw_pack,
                    "raw_product_code": cell(row, fields.get("code")),
                    "raw_product_name": cell(row, fields["name"]),
                    "raw_alt_names": [cell(row, c) for c in fields.get("alt", [])],
                    "alt_name_cells": [
                        f"{openpyxl.utils.get_column_letter(c)}{row_index}"
                        for c in fields.get("alt", [])
                    ],
                    "template_id": profile.get("id"),
                    "template_key": definition["key"],
                    "template_version": metadata["version"],
                    "supplier_code": cell(row, fields.get("supplier_code")),
                    "requires_packing_review": not pack,
                    "formula": formula,
                }
                if isinstance(code, float) and code.is_integer():
                    code = int(code)
                grouped[customer].append(
                    CanonicalDraftItem(
                        raw_text=f"{code or ''} {name or ''}: {raw}"[:4096],
                        product_code=str(code) if code is not None else None,
                        product_name=str(name or "")[:512],
                        alt_names=[
                            str(cell(row, c)) for c in fields.get("alt", []) if cell(row, c)
                        ],
                        quantity=amount,
                        unit=unit_key(unit),
                        pack_size=pack,
                        quantity_issues=problems,
                        source=source,
                        action=action,
                    )
                )
                if sum(map(len, grouped.values())) > limits.MAX_ITEMS:
                    raise WorkLimit("Document exceeds 20000 order entries")
    if not grouped:
        grouped[definition.get("client_code") or "REVIEW"] = []
    return validate_drafts(
        [
            CanonicalOrderDraft(
                source_file=Path(original_name or path).name,
                channel=IngestionChannel.LEGACY_XLS
                if Path(path).suffix.lower() == ".xls"
                else IngestionChannel.EXCEL_TEXT
                if definition["kind"] == "text"
                else IngestionChannel.EXCEL_TABULAR,
                client_code=code,
                main_customer_code=definition["main_customer_code"],
                items=items,
                metadata={"template": metadata},
            )
            for code, items in grouped.items()
        ]
    )
