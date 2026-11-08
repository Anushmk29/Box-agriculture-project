"""Build reference mappings from supplied samples, without changing the workbooks."""

import hashlib
import json
import re
from pathlib import Path

import openpyxl
import xlrd

ROOT = Path(__file__).resolve().parents[2]
STANDBY = {"440T.xlsx", "51H-2.xlsx", "86L.ods"}


def add_customer_samples(profiles):
    """Register the reviewed customer layouts without altering their source files."""
    for path in sorted((ROOT / "documents/excel files of all cutomer").iterdir()):
        if path.name in STANDBY or path.suffix.lower() not in {".xlsx", ".xls"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if any(p["sha256"] == digest for p in profiles):
            continue
        if path.suffix == ".xls":
            book = xlrd.open_workbook(path)
            sheets = [(s.name, [s.row_values(i) for i in range(s.nrows)]) for s in book.sheets()]
        else:
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                sheets = [(s.title, list(s.iter_rows(values_only=True))) for s in book]
            finally:
                book.close()
        match = re.match(r"^(\d+[HLT](?:\s*-\s*\d+)?)", path.stem)
        if not match and not any(token in path.name for token in ("Tonio", "NGA")) and path.name != "Agriculture - 07 Sept 26.xlsx":
            raise ValueError(f"No reviewed customer identity for {path.name}; configure it before rebuilding")
        client = re.sub(r"\s+", "", match[1]) if match else None
        key = client or ("TONIO-MATRIX" if "Tonio" in path.name else
                         "NGA-REGIONAL" if "NGA" in path.name else "CHOKDEE")
        main = "TONIO" if "Tonio" in path.name else "CHOKDEE" if key == "CHOKDEE" else "NGA"
        identity = [client] if client else ["tonio"] if main == "TONIO" else ["nga"] if main == "NGA" else ["chokdee", "giggling squid", "Agriculture"]
        identity += {"59H": ["Jin Cheng"], "705H": ["Thai Grønt Deli"], "90L": ["IMS DRAMMEN"]}.get(client, [])
        specs = []
        review = []
        numeric = None
        kind = "text" if client in {"427T", "82L"} else "table"
        if kind == "text":
            specs = [dict(reference_sheet=sheets[0][0], anchors={"1": client},
                          fields=dict(name=1, quantity=1), customers=None)]
        else:
            selected = sheets[:3] if key == "NGA-REGIONAL" else sheets[:1]
            for sheet_name, rows in selected:
                if key == "NGA-REGIONAL":
                    header, fields = 2, dict(code=1, name=2, alt=[3], pack=4)
                    customers = {c: str(v).strip() for c, v in enumerate(rows[1], 1)
                                 if c >= 6 and re.fullmatch(r"\d+[HLT](?:-\d+)?", str(v or "").strip())}
                elif main == "TONIO":
                    header, fields = 3, dict(code=1, name=3, alt=[2], pack=4)
                    customers = {c: str(v).strip() for c, v in enumerate(rows[2], 1)
                                 if c >= 5 and v and str(v).strip().casefold() != "total"}
                else:
                    header, labels = next(
                        (r, {str(v or "").strip().casefold(): c for c, v in enumerate(row, 1) if v})
                        for r, row in enumerate(rows[:20], 1)
                        if any(str(v or "").strip().casefold() in {"product name", "description (english)"} for v in row)
                    )
                    name = labels.get("product name") or labels["description (english)"]
                    quantities = [c for label, c in labels.items() if label == "order" or label.startswith("order ") or label.startswith("amount of order")]
                    quantity = max(quantities)
                    if len(quantities) > 1 and client != "705H":
                        raise ValueError(f"Select the shipment order column for {path.name}")
                    fields = dict(name=name, quantity=quantity)
                    customers = None
                    alt = [labels[label] for label in ("viet", "thai", "description thai") if label in labels]
                    if main == "CHOKDEE":
                        alt = [3]
                    if alt:
                        fields["alt"] = alt
                    if name == 6:
                        fields.update(code=4, supplier_code=5, pack=9, adjacent=10)
                        numeric = "g"
                    elif name == 4:
                        fields.update(code=2, supplier_code=3, pack=7, adjacent=8)
                        numeric = "g"
                    elif name == 3:
                        fields.update(code=2, customer_product_code=1, description_pack=4)
                    else:
                        first_code = next((row[0] for row in rows[header:] if row and row[0] not in (None, "")), None)
                        fields["customer_product_code" if re.fullmatch(r"\d{1,3}(?:\.0)?", str(first_code)) else "code"] = 1
                        if "description thai" in labels:
                            fields["description_pack"] = labels["description thai"]
                    for label in ("packing", "packge", "packing weight", "packing size (gr.)"):
                        if label in labels:
                            fields["pack"] = labels[label]
                    if "packing size (gr.)" in labels:
                        numeric = "g"
                    if "unit" in labels:
                        fields["unit"] = labels["unit"]
                    elif name == 3 and any(len(row) >= 8 and str(row[7]).strip().lower() in {"kgs.", "kg"} for row in rows[header:header + 10]):
                        fields["unit"] = 8
                    for label in ("noted", "notes", "remark", "หมายเหตุ"):
                        if label in labels:
                            fields["notes"] = labels[label]
                    if client == "705H":
                        fields["unit"] = labels["unit"]
                        review.append("705H contains historical Order/Delivery/stock columns. Confirm the shipment order column before using this mapping.")
                columns = [c for c in fields.values() if isinstance(c, int)] + list(customers or {})
                anchors = {str(c): str(rows[header - 1][c - 1]).strip() for c in columns
                           if rows[header - 1][c - 1] not in (None, "")}
                mapped = set(columns) | set(fields.get("alt", []))
                ignored = {str(c): str(v).strip() for c, v in enumerate(rows[header - 1], 1)
                           if c not in mapped and v not in (None, "")}
                specs.append(dict(reference_sheet=sheet_name, anchors=anchors, fields=fields, customers=customers,
                                  ignored_columns=ignored))
        definition = dict(key=key, main_customer_code=main, client_code=client or ("CHOKDEE" if main == "CHOKDEE" else None),
                          identity_tokens=identity, kind=kind, sheets=specs, default_unit=None,
                          numeric_pack_unit=numeric, sample=path.name, sha256=digest,
                          sample_directory="excel files of all cutomer")
        if client == "427T":
            definition["text_quantity_separator"] = "-"
        if review:
            definition["review_issues"] = review
        contract = {k: v for k, v in definition.items() if k not in {"key", "sample", "sha256", "sample_directory"}}
        duplicate = next((p for p in profiles if {k: v for k, v in p.items()
                         if k not in {"key", "sample", "sha256", "sample_directory", "additional_samples"}} == contract), None)
        if duplicate:
            duplicate.setdefault("additional_samples", []).append(dict(sample=path.name, sha256=digest))
            continue
        suffix = 2
        while any(p["key"] == definition["key"] for p in profiles):
            definition["key"] = f"{key}-LAYOUT-{suffix}"
            suffix += 1
        profiles.append(definition)
    return profiles


def build():
    profiles = []
    for path in sorted((ROOT / "documents/new input format").iterdir()):
        if path.suffix.lower() not in {".xlsx", ".xls"}:
            continue
        name = path.name
        code = name.split()[0].split(".")[0]
        main = "NGA"
        unit = None
        numeric = None
        kind = "table"
        identity = [code] if code[-1:] in "THL" else []
        if code in {"19T", "412T", "68H"}:
            specs = [
                (
                    0,
                    4,
                    dict(
                        code=4,
                        supplier_code=5,
                        name=6,
                        alt=[7, 8],
                        pack=9,
                        adjacent=10,
                        quantity=11,
                        unit=12,
                    ),
                    None,
                )
            ]
            identity += [{"19T": "003319", "412T": "003306", "68H": "003468"}[code]]
            numeric = "g"
        elif code == "20T":
            kind = "text"
            specs = [(0, 3, dict(name=1, quantity=1), None)]
        elif code == "40T":
            specs = [(0, 2, dict(name=2, alt=[3, 4], quantity=6, unit=5), None)]
        elif code == "50H":
            numeric = "kg"
            specs = [(0, 2, dict(name=2, alt=[3, 4], pack=5, unit=6, quantity=7), None)]
        elif code in {"66H", "76H", "931L"}:
            q = 5 if code == "931L" else 6
            specs = [
                (
                    0,
                    2,
                    dict(
                        code=2,
                        customer_product_code=1,
                        name=3,
                        alt=[4],
                        description_pack=4,
                        quantity=q,
                        unit=6 if q == 5 else 8,
                    ),
                    None,
                )
            ]
        elif code == "77H":
            identity += ["003477"]
            specs = [(0, 2, dict(code=1, name=2, quantity=4), None)]
        elif code == "87L":
            identity += ["hoang asiamat"]
            specs = [(0, 2, dict(name=2, alt=[3, 4], pack=5, quantity=6), None)]
        elif code == "928L":
            numeric = "g"
            specs = [
                (
                    0,
                    2,
                    dict(
                        code=2,
                        supplier_code=3,
                        name=4,
                        alt=[5, 6],
                        pack=7,
                        adjacent=8,
                        quantity=9,
                        unit=10,
                    ),
                    None,
                )
            ]
        elif "Ly-Thanh" in name:
            main = "LY-THANH"
            code = (
                "LY-FRIBOURG"
                if "FRIBOURG" in name
                else "LY-VEVEY"
                if "VEVEY" in name
                else "LY-MORGES"
            )
            identity = [code.split("-")[-1]]
            specs = [
                (
                    0,
                    8 if path.suffix == ".xls" else 10,
                    dict(code=2, name=4, alt=[3, 5], pack=6, quantity=7, unit=8),
                    None,
                )
            ]
        elif "CHAOTHAI" in name or "PINTO" in name:
            main = "CHAOTHAI" if "CHAOTHAI" in name else "PINTO-THAI"
            code = (
                "CHAOTHAI-JCT1"
                if "JCT1" in name
                else "CHAOTHAI-JCT"
                if "CHAOTHAI" in name
                else "PINTO-MLD"
            )
            identity = [code.split("-")[-1]]
            numeric = "g"
            unit = "kg" if "PINTO" in name else None
            specs = [(0, 11, dict(code=1, name=2, alt=[3], pack=4, quantity=5), None)]
        elif "P-PUI" in name or "YACHI" in name:
            main = code = "P-PUI" if "P-PUI" in name else "YACHI"
            identity = [code]
            numeric = "g"
            specs = [(0, 11, dict(code=2, name=3, alt=[4, 5], pack=6, quantity=7, unit=8), None)]
        elif "Hungfat" in name:
            main = "HUNGFAT"
            code = "HUNGFAT-MATRIX"
            identity = ["hungfat"]
            numeric = "g"
            unit = "kg"
            specs = [
                (
                    0,
                    5,
                    dict(code=1, name=2, alt=[3, 4], pack=5),
                    {6: "HUNGFAT-101", 7: "HUNGFAT-103", 8: "HUNGFAT-110", 9: "HUNGFAT-116"},
                )
            ]
        elif "Tonio" in name:
            main = "TONIO"
            code = "TONIO-MATRIX"
            identity = ["tonio"]
            specs = [(0, 3, dict(code=1, name=3, alt=[2], pack=4), "header")]
        elif "NGA" in name:
            code = "NGA-REGIONAL"
            identity = ["nga"]
            specs = [(i, 2, dict(code=1, name=2, alt=[3], pack=4), "header") for i in range(3)]
        elif "LY" in name:
            main = "LY-THANH"
            code = "LY-REGIONAL"
            identity = ["ly-thanh"]
            specs = [(0, 3, dict(code=1, name=2, alt=[3], pack=4), {6: "LY-MORGES", 7: "LY-VEVEY"})]
        else:
            raise ValueError(f"No reviewed mapping for {name}")
        if path.suffix == ".xls":
            wb = xlrd.open_workbook(path)
            sheets = [(s.name, [s.row_values(i) for i in range(s.nrows)]) for s in wb.sheets()]
        else:
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            sheets = [(s.title, list(s.iter_rows(values_only=True))) for s in wb]
            wb.close()
        definitions = []
        for sheet_index, header, fields, customers in specs:
            sheet_name, rows = sheets[sheet_index]
            values = rows[header - 1]
            if customers == "header":
                start, end = (5, 118) if main == "TONIO" else (6, {0: 10, 1: 8, 2: 14}[sheet_index])
                customers = {
                    c: str(values[c - 1]).strip() for c in range(start, end + 1) if values[c - 1]
                }
            cols = list(dict.fromkeys(v for v in fields.values() if isinstance(v, int)))
            if customers:
                cols.extend(customers)
            anchors = {str(c): str(values[c - 1]).strip() for c in cols if values[c - 1]}
            if not anchors and customers:
                anchors = {
                    str(c): str(values[c - 1]).strip()
                    for c in [1, 4, min(customers)]
                    if values[c - 1]
                }
            definitions.append(
                dict(
                    reference_sheet=sheet_name, anchors=anchors, fields=fields, customers=customers
                )
            )
        profiles.append(
            dict(
                key=code,
                main_customer_code=main,
                client_code=None if any(s["customers"] for s in definitions) else code,
                identity_tokens=identity,
                kind=kind,
                sheets=definitions,
                default_unit=unit,
                numeric_pack_unit=numeric,
                sample=name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    add_customer_samples(profiles)
    for profile in profiles:
        if profile["key"].startswith("NGA-REGIONAL"):
            for sheet in profile["sheets"]:
                # Reviewed NGA samples have an unlabelled reference catalogue beyond the selling prices.
                end = max(map(int, sheet["customers"]))
                sheet.setdefault("ignored_columns", {}).update({str(end + offset): "" for offset in (3, 5, 6, 7)})
    output = ROOT / "backend/app/assets/excel_templates.json"
    output.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Built {len(profiles)} Excel format definitions")


if __name__ == "__main__":
    build()
