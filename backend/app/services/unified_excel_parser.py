import os
import re
from typing import Any

import openpyxl
import xlrd

from app.core import work_limits as limits
from app.core.document_limits import inspect_document
from app.core.work_limits import WorkLimit
from app.modules.orders.quantities import normalize_pack, source_amount, unit_key
from app.services.canonical_schema import (
    ActionType,
    CanonicalDraftItem,
    CanonicalOrderDraft,
    IngestionChannel,
)


class UnifiedExcelParser:
    """
    Unified Adaptive Excel Parser.
    Seamlessly ingests:
      1. Modern .xlsx and legacy binary .xls (via xlrd)
      2. Unstructured plain text in Excel (e.g. 20T.xlsx) with European comma decimals and 'STOP' cancels
      3. Sub-shop order forms (19T, 40T, 50H, 66H, 68H, 76H, 77H, 87L, 928L, 931L)
      4. Single-client tabular order sheets (Ly-Thanh Morges/Vevey/Fribourg, JCT, PINTO, Hungfat)
      5. Master multi-client horizontal matrices (Order Tonio, YACHI, P-PUI)
      6. Regional consolidation workbooks (ราการขนม NGA, รายการขนม LY)
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        inspect_document(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Excel file not found at: {file_path}")
        self.ext = os.path.splitext(file_path)[1].lower()
        self.file_name = os.path.basename(file_path)
        self.file_name = re.sub(r"^[0-9a-fA-F-]{36}_", "", self.file_name)

    def _read_sheet_rows(self, ws: Any, is_xls: bool) -> list[list[Any]]:
        """Extracts rows as a 2D list regardless of .xlsx or .xls format."""
        rows = ws.nrows if is_xls else ws.max_row
        columns = ws.ncols if is_xls else ws.max_column
        if (rows > limits.MAX_ROWS or columns > limits.MAX_COLUMNS or rows * columns > limits.MAX_CELLS):
            raise WorkLimit("Worksheet exceeds its safe row/column/cell budget")
        if is_xls:
            rows = []
            for r in range(ws.nrows):
                rows.append(ws.row_values(r))
            return rows
        else:
            return list(ws.iter_rows(values_only=True))

    def _get_sheet_names(self) -> list[str]:
        if self.ext == ".xls":
            wb = xlrd.open_workbook(self.file_path)
            return wb.sheet_names()
        else:
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            return wb.sheetnames

    def parse(self) -> list[CanonicalOrderDraft]:
        """
        Detects workbook archetype and returns a list of CanonicalOrderDraft objects.
        """
        # 1. Check if unstructured single-column text (e.g. 20T.xlsx)
        if self._is_unstructured_text():
            return [self._parse_unstructured_text()]

        # 2. Check if sub-shop NGA form (e.g. 19T, 66H, 68H, etc.)
        sub_shop_code = self._detect_sub_shop_code()
        if sub_shop_code:
            draft = self._parse_sub_shop_sheet(sub_shop_code)
            if draft and len(draft.items) > 0:
                return [draft]

        # 3. Check if Tabular Store / Single Client Form (Ly-Thanh, JCT, PINTO, Hungfat)
        if "hungfat" in self.file_name.casefold():
            return self._parse_hungfat()
        tabular_draft = self._parse_single_client_tabular()
        if tabular_draft and len(tabular_draft.items) > 0:
            return [tabular_draft]

        # 4. Fallback to Multi-Client Matrix
        return self._parse_multi_client_matrix()

    def _detect_sub_shop_code(self) -> str | None:
        """Detects if file represents an NGA sub-shop (e.g. 19T, 66H, 87L)."""
        base_no_ext = os.path.splitext(self.file_name)[0].split()[0].upper()
        if re.match(r"^\d+[THL]$", base_no_ext):
            return base_no_ext
        return None

    def _is_unstructured_text(self) -> bool:
        """Checks if Sheet1 has text only in column 1 (like 20T.xlsx)."""
        if self.ext == ".xls":
            return False
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        ws = wb.active
        # Check max column and row count
        if ws.max_column <= 2:
            non_empty_col1 = 0
            for r in ws.iter_rows(values_only=True):
                if r and r[0] is not None and str(r[0]).strip():
                    non_empty_col1 += 1
            if non_empty_col1 >= 10:
                return True
        return False

    def _parse_unstructured_text(self) -> CanonicalOrderDraft:
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        ws = wb.active
        client_code = os.path.splitext(self.file_name)[0]
        items: list[CanonicalDraftItem] = []

        for row in ws.iter_rows(values_only=True):
            if not row or row[0] is None:
                continue
            line = str(row[0]).strip()
            if not line or any(
                kw in line.lower() for kw in ["prod. no", "product name", "bestilling"]
            ):
                continue

            # Check if header sets client code (e.g. "20T")
            if re.match(r"^\d+[THL]$", line):
                client_code = line
                continue

            # Cancellation check: ends with STOP
            is_stopped = bool(re.search(r"\bSTOP\b", line, re.IGNORECASE))
            action = ActionType.CANCEL if is_stopped else ActionType.ORDER

            # Clean line for quantity extraction: "11028 Grønn Papaya 1,5 kg" or "0,200 gms"
            # Support European comma decimal: e.g. "1,5", "0,600", "0.080"
            qty_match = re.search(
                r"(\d+(?:[.,]\d+)?)\s*(kg|kgs|gms|g|pkts|packs|pcs)?(?:\s+STOP)?$",
                line,
                re.IGNORECASE,
            )
            qty = 1.0
            unit = "kg"
            prod_part = line
            if qty_match:
                raw_qty = qty_match.group(1).replace(",", ".")
                qty = float(raw_qty)
                unit = (qty_match.group(2) or "kg").lower()
                prod_part = line[: qty_match.start()].strip()
            elif is_stopped:
                prod_part = re.sub(r"\bSTOP\b", "", line, flags=re.IGNORECASE).strip()
                qty = 0.0

            # Normalize gms/g to kg if < 1.0 or large
            if unit in ["gms", "g"]:
                qty = qty / 1000.0
                unit = "kg"

            # Extract leading code if present (e.g. 11028, 11-008, EL004, RQV533)
            code_m = re.match(r"^([A-Z0-9]{2,7}-?[A-Z0-9]{0,4})\s+(.*)$", prod_part)
            prod_code = None
            prod_name = prod_part
            if code_m and not code_m.group(1).isalpha():
                prod_code = code_m.group(1).strip()
                prod_name = code_m.group(2).strip()

            items.append(
                CanonicalDraftItem(
                    raw_text=line,
                    product_code=prod_code,
                    product_name=prod_name,
                    quantity=qty,
                    unit=unit,
                    action=action,
                    confidence=0.88,
                    notes="Parsed from single-column text",
                )
            )

        return CanonicalOrderDraft(
            source_file=self.file_name,
            channel=IngestionChannel.EXCEL_TEXT,
            client_code=client_code,
            client_name=f"Sub-Shop {client_code}",
            main_customer_code="NGA",
            route_line=f"NGA-{client_code[-1]}" if client_code[-1] in "THL" else "NGA",
            destination_country="Norway",
            items=items,
        )

    def _parse_sub_shop_sheet(self, shop_code: str) -> CanonicalOrderDraft:
        is_xls = self.ext == ".xls"
        if is_xls:
            wb = xlrd.open_workbook(self.file_path)
            ws = wb.sheet_by_index(0)
            rows = self._read_sheet_rows(ws, True)
        else:
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            ws = wb.active
            rows = self._read_sheet_rows(ws, False)

        items: list[CanonicalDraftItem] = []
        header_row_idx = None
        order_col_idx = None
        name_col_idx = None
        code_col_idx = None
        pack_col_idx = None
        unit_col_idx = None

        # Scan first 6 rows to locate header row
        for r_idx, r in enumerate(rows[:6]):
            for c_idx, cell in enumerate(r):
                c_str = str(cell or "").strip().lower()
                if c_str in ["order", "order quantity", "bestilling", "ham 1"]:
                    header_row_idx = r_idx
                    order_col_idx = c_idx
                if any(kw in c_str for kw in ["product name", "description", "รายการ"]):
                    name_col_idx = c_idx
                if c_str in ["code", "item", "item code", "product no.", "รหัส"]:
                    if code_col_idx is None:
                        code_col_idx = c_idx
                if c_str in ["pack", "packge", "packing", "ขนาดแพค"]:
                    pack_col_idx = c_idx
                if c_str == "unit":
                    unit_col_idx = c_idx

        # If order column not found by keyword, fallback to looking for non-empty column in shop 66H/76H
        if order_col_idx is None:
            for r_idx, r in enumerate(rows[:4]):
                for c_idx, cell in enumerate(r):
                    if str(cell or "").strip().upper() == shop_code:
                        order_col_idx = c_idx
                        header_row_idx = r_idx
                        break

        # Fallback defaults for standard NGA 54-col sheets
        if order_col_idx is None:
            order_col_idx = 10
            header_row_idx = 3

        if name_col_idx is None:
            name_col_idx = 2 if len(rows[0]) > 2 else 1

        start_row = (header_row_idx + 1) if header_row_idx is not None else 4

        for r_idx in range(start_row, len(rows)):
            row = rows[r_idx]
            if not row or len(row) <= order_col_idx:
                continue

            order_val = row[order_col_idx]
            if order_val is None or str(order_val).strip() == "" or str(order_val).strip() == "0":
                continue

            # Convert quantity
            default_unit = (
                str(row[unit_col_idx] or "unknown") if unit_col_idx is not None else "unknown"
            )
            qty, parsed_unit = source_amount(order_val, default_unit)
            if qty == 0:
                continue

            prod_code = (
                str(row[code_col_idx]).strip()
                if code_col_idx is not None and code_col_idx < len(row) and row[code_col_idx]
                else None
            )
            prod_name = (
                str(row[name_col_idx]).strip()
                if name_col_idx is not None and name_col_idx < len(row) and row[name_col_idx]
                else None
            )
            pack_size = (
                str(row[pack_col_idx]).strip()
                if pack_col_idx is not None and pack_col_idx < len(row) and row[pack_col_idx]
                else None
            )
            raw_pack = pack_size
            row_unit = parsed_unit
            # NGA's 50H format prints kg beside decimal numeric Packing values.
            numeric_unit = (
                "kg"
                if self.file_name.casefold().startswith("50h")
                else "g"
                if pack_col_idx is not None
                and str(rows[header_row_idx][pack_col_idx]).casefold() == "packge"
                else None
            )
            adjacent = (
                row[pack_col_idx + 1]
                if pack_col_idx is not None
                and pack_col_idx + 2 == order_col_idx
                and isinstance(row[pack_col_idx + 1], (int, float))
                else None
            )
            pack_size, issues = normalize_pack(
                pack_size, numeric_unit=numeric_unit, adjacent_kg=adjacent
            )

            # Clean synthetic floats (e.g. 11001.0 -> 11001)
            if prod_code and prod_code.endswith(".0"):
                prod_code = prod_code[:-2]

            items.append(
                CanonicalDraftItem(
                    raw_text=f"{prod_code} | {prod_name} | {qty}",
                    product_code=prod_code,
                    product_name=prod_name,
                    pack_size=pack_size,
                    quantity=qty,
                    unit=unit_key(row_unit),
                    quantity_issues=issues,
                    source={
                        "sheet": ws.name if is_xls else ws.title,
                        "row": r_idx + 1,
                        "raw_pack_size": raw_pack,
                        "raw_quantity": order_val,
                        "raw_unit": default_unit,
                        "adjacent_pack_kg": adjacent,
                    },
                    action=ActionType.ORDER,
                    confidence=0.95,
                    notes=f"Sub-shop sheet {shop_code}",
                )
            )

        return CanonicalOrderDraft(
            source_file=self.file_name,
            channel=IngestionChannel.LEGACY_XLS if is_xls else IngestionChannel.EXCEL_TABULAR,
            client_code=shop_code,
            client_name=f"Sub-Shop {shop_code}",
            main_customer_code="NGA",
            route_line=f"NGA-{shop_code[-1]}" if shop_code[-1] in "THL" else "NGA",
            destination_country="Norway",
            items=items,
        )

    def _parse_single_client_tabular(self) -> CanonicalOrderDraft | None:
        """Parses Ly-Thanh, JCT, PINTO, and Hungfat style tabular sheets."""
        is_xls = self.ext == ".xls"
        if is_xls:
            wb = xlrd.open_workbook(self.file_path)
            ws = wb.sheet_by_index(0)
            rows = self._read_sheet_rows(ws, True)
        else:
            wb = openpyxl.load_workbook(self.file_path, data_only=True)
            # Find relevant sheet
            target_sheet = (
                "Order form"
                if "Order form" in wb.sheetnames
                else (
                    "KUNG"
                    if "KUNG" in wb.sheetnames
                    else ("CHAOTHAI" if "CHAOTHAI" in wb.sheetnames else wb.sheetnames[0])
                )
            )
            ws = wb[target_sheet]
            rows = self._read_sheet_rows(ws, False)

        header_str = " ".join([str(c) for r in rows[:10] for c in r if c])
        lower_header = header_str.lower()

        client_code = "UNKNOWN"
        client_name = None
        main_customer = None
        route = None
        country = None

        if (
            "ly thanh" in lower_header
            or "morges" in lower_header
            or "vevey" in lower_header
            or "fribourg" in lower_header
        ):
            main_customer = "LY-THANH"
            country = "Switzerland"
            route = "SWISS"
            if "vevey" in lower_header or "vevey" in self.file_name.lower():
                client_code = "LY-VEVEY"
                client_name = "Ly-Thanh Sarl Vevey"
            elif "fribourg" in lower_header or "fribourg" in self.file_name.lower():
                client_code = "LY-FRIBOURG"
                client_name = "Ly-Thanh Sarl Fribourg"
            else:
                client_code = "LY-MORGES"
                client_name = "Ly-Thanh Sarl Morges"

        elif "chaothai" in lower_header or "jct" in lower_header:
            main_customer = "CHAOTHAI"
            country = "Japan"
            route = "JCT"
            if "jct1" in lower_header or "jct1" in self.file_name.lower():
                client_code = "CHAOTHAI-JCT1"
                client_name = "Chaothai Co., Ltd. (JCT1)"
            else:
                client_code = "CHAOTHAI-JCT"
                client_name = "Chaothai Co., Ltd. (JCT)"

        elif "pinto" in lower_header or "mld" in lower_header:
            main_customer = "PINTO-THAI"
            client_code = "PINTO-MLD"
            client_name = "Pinto Thai / Ahmed Alman"
            country = "Maldives"
            route = "MLD"

        elif "hungfat" in lower_header:
            main_customer = "HUNGFAT"
            client_code = "HUNGFAT-SWE"
            client_name = "Hungfat Trading Sweden"
            country = "Sweden"
            route = "SWE"

        elif (
            "yachi" in (lower_header + self.file_name.casefold())
            or "p-pui" in self.file_name.casefold()
        ):
            client_code = (
                "YACHI" if "yachi" in (lower_header + self.file_name.casefold()) else "P-PUI"
            )
            client_name = main_customer = route = client_code

        if client_code == "UNKNOWN":
            return None

        # Find table header row
        table_hdr_idx = None
        code_idx = None
        name_idx = None
        qty_idx = None
        unit_idx = None

        pack_idx = None
        for r_idx, r in enumerate(rows[:15]):
            row_vals = [str(c or "").strip().lower() for c in r]
            if any(kw in row_vals for kw in ["code", "รหัส", "item", "code no", "product codes"]):
                table_hdr_idx = r_idx
                for c_idx, val in enumerate(row_vals):
                    if val in ["code", "รหัส", "code no", "product codes"]:
                        code_idx = c_idx
                    elif any(
                        kw in val
                        for kw in [
                            "thai name",
                            "product name",
                            "รายการ",
                            "name eng",
                            "english name",
                        ]
                    ):
                        if name_idx is None:
                            name_idx = c_idx
                    elif any(
                        kw in val
                        for kw in [
                            "quantity",
                            "order",
                            "agrisen (kg)",
                            "amont to order",
                            "amount to order",
                        ]
                    ):
                        qty_idx = c_idx
                    elif val in ["unit", "หน่วย"] or (
                        val == "weight" and any("packing" in v for v in row_vals)
                    ):
                        unit_idx = c_idx
                    elif (
                        val in ["น้ำหนัก", "weight", "packing", "ขนาดแพค", "pack"]
                        or "packing /" in val
                    ):
                        pack_idx = c_idx
                break

        # Fallback column positions if not explicitly labeled
        if qty_idx is None:
            # Dynamically scan columns in rows 12 to 25 to find the column containing numeric quantities
            # Skip pack_idx column!
            best_col = None
            max_num_count = 0
            for c in range(3, min(10, len(rows[0]))):
                if c == pack_idx:
                    continue
                num_count = sum(
                    1
                    for r in rows[10:35]
                    if len(r) > c
                    and r[c] is not None
                    and isinstance(r[c], (int, float))
                    and r[c] > 0
                )
                if num_count > max_num_count:
                    max_num_count = num_count
                    best_col = c
            qty_idx = best_col if best_col is not None else (len(rows[0]) - 1)

        if code_idx is None:
            code_idx = 0 if is_xls else 1
        if name_idx is None:
            name_idx = 1 if is_xls else 2

        items: list[CanonicalDraftItem] = []
        start_row = (table_hdr_idx + 1) if table_hdr_idx is not None else 8

        for r_idx in range(start_row, len(rows)):
            row = rows[r_idx]
            if not row or len(row) <= qty_idx:
                continue

            val = row[qty_idx]
            if val is None or str(val).strip() in ["", "0", "0.0", "#NAME?"]:
                continue

            try:
                qty = float(str(val).strip().replace(",", "."))
            except ValueError:
                continue

            p_code = str(row[code_idx]).strip() if code_idx < len(row) and row[code_idx] else None
            p_name = str(row[name_idx]).strip() if name_idx < len(row) and row[name_idx] else None
            p_unit = (
                str(row[unit_idx]).strip()
                if unit_idx is not None and unit_idx < len(row) and row[unit_idx]
                else ("kg" if main_customer == "PINTO-THAI" else "unknown")
            )
            raw_pack = row[pack_idx] if pack_idx is not None and pack_idx < len(row) else None
            size, issues = normalize_pack(
                raw_pack,
                numeric_unit="g"
                if client_code in {"YACHI", "P-PUI", "PINTO-MLD", "CHAOTHAI-JCT", "CHAOTHAI-JCT1"}
                else None,
            )

            if not p_code and not p_name:
                continue

            if p_code and p_code.endswith(".0"):
                p_code = p_code[:-2]

            items.append(
                CanonicalDraftItem(
                    raw_text=f"{p_code} | {p_name} | {qty} {p_unit}",
                    product_code=p_code,
                    product_name=p_name,
                    pack_size=size,
                    quantity=qty,
                    unit=unit_key(p_unit),
                    quantity_issues=issues,
                    source={
                        "sheet": ws.name if is_xls else ws.title,
                        "row": r_idx + 1,
                        "raw_pack_size": raw_pack,
                        "raw_quantity": val,
                        "raw_unit": p_unit,
                    },
                    action=ActionType.ORDER,
                    confidence=0.96,
                )
            )

        return CanonicalOrderDraft(
            source_file=self.file_name,
            channel=IngestionChannel.LEGACY_XLS if is_xls else IngestionChannel.EXCEL_TABULAR,
            client_code=client_code,
            client_name=client_name,
            main_customer_code=main_customer,
            route_line=route,
            destination_country=country,
            items=items,
        )

    def _parse_multi_client_matrix(self) -> list[CanonicalOrderDraft]:
        """Parses large horizontal multi-client matrices like Order Tonio."""
        from app.modules.orders.intake import matrix_to_drafts
        from app.services.matrix_parser import MatrixOrderParser

        return matrix_to_drafts(
            MatrixOrderParser(self.file_path).parse_all_clients(), self.file_name
        )

    def _parse_hungfat(self):
        """Each named order column is a customer; the printed row unit owns its meaning."""
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        header = next(
            (
                i
                for i, r in enumerate(rows[:12])
                if sum(bool(re.match(r"order\s*-?\s*\d", str(v or ""), re.I)) for v in r) > 1
            ),
            None,
        )
        if header is None:
            wb.close()
            raise ValueError("Hungfat order columns were not found")
        # Prefer the repeated complete header rather than the summary at row 1.
        for i, r in enumerate(rows[:12]):
            if sum(bool(re.match(r"order\s*-?\s*\d", str(v or ""), re.I)) for v in r) >= 4:
                header = i
                break
        drafts = []
        for c, label in enumerate(rows[header]):
            match = re.fullmatch(r"order\s*-?\s*(\d+)", str(label or "").strip(), re.I)
            if not match:
                continue
            items = []
            for n, r in enumerate(rows[header + 1 :], header + 2):
                if not r[0] or not r[1] or not isinstance(r[c], (int, float)) or r[c] <= 0:
                    continue
                size, issues = normalize_pack(r[4], numeric_unit="g")
                raw_unit = r[10] if len(r) > 10 else "unknown"
                items.append(
                    CanonicalDraftItem(
                        raw_text=f"{r[0]} | {r[1]} | {r[c]} {raw_unit}",
                        product_code=str(r[0]),
                        product_name=str(r[1]),
                        alt_names=[str(v) for v in r[2:4] if v],
                        pack_size=size,
                        quantity=r[c],
                        unit=unit_key(str(raw_unit or "unknown")),
                        quantity_issues=issues,
                        source={
                            "sheet": ws.title,
                            "row": n,
                            "column": c + 1,
                            "raw_pack_size": r[4],
                            "raw_quantity": r[c],
                            "raw_unit": raw_unit,
                        },
                    )
                )
            if items:
                drafts.append(
                    CanonicalOrderDraft(
                        source_file=self.file_name,
                        channel=IngestionChannel.EXCEL_TABULAR,
                        client_code=f"HUNGFAT-{match[1]}",
                        main_customer_code="HUNGFAT",
                        route_line="SWE",
                        items=items,
                    )
                )
        wb.close()
        return drafts
