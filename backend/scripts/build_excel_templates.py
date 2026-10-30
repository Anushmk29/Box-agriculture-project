"""Build reference mappings from supplied samples, without changing the workbooks."""

import hashlib
import json
from pathlib import Path

import openpyxl
import xlrd

ROOT = Path(__file__).resolve().parents[2]


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
    output = ROOT / "backend/app/assets/excel_templates.json"
    output.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventoried {len(profiles)} Excel reference files")


if __name__ == "__main__":
    build()
