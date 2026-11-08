import os
import re
from typing import Any

import openpyxl

from app.core.document_limits import inspect_document
from app.modules.orders.quantities import normalize_pack, unit_key


class MatrixOrderParser:
    """
    Robust Streaming Matrix & Tabular Order Parser with Dynamic Floating Anchor Detection.
    Handles both:
      1. Large Multi-Client Horizontal Matrices (e.g. Order Tonio, NGA matrix) where header row
         placement and client columns shift dynamically across sheets.
      2. Single-Client Tabular Order Sheets (e.g. Single Customer Order Template, Chaothai, Hungfat)
         where produce items are listed row-by-row with an explicit 'Order' / 'Quantity' column.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        inspect_document(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Order matrix file not found at: {file_path}")

    def _get_client_code_from_order_info(self, wb) -> str | None:
        """Extracts declared client code from 'Order Info' sheet if present."""
        if "Order Info" in wb.sheetnames:
            ws_info = wb["Order Info"]
            sub_code = None
            main_code = None
            for r in range(1, min(25, (ws_info.max_row or 20) + 1)):
                k = str(ws_info.cell(r, 1).value or "").strip().lower()
                v = str(ws_info.cell(r, 2).value or "").strip()
                if "sub-customer" in k and v:
                    sub_code = v
                elif "main customer" in k and v:
                    main_code = v
            if sub_code:
                return sub_code
            if main_code:
                return main_code
        return None

    def _detect_client_from_sheet(self, ws, sheet_name: str) -> str:
        """Infers client code from sheet top metadata, sheet name, or file basename."""
        for r in range(1, min(6, (ws.max_row or 5) + 1)):
            for c in range(1, min(8, (ws.max_column or 8) + 1)):
                val = str(ws.cell(r, c).value or "").strip()
                if any(kw in val.lower() for kw in ["ชื่อลูกค้า", "customer name", "client:"]):
                    next_val = str(ws.cell(r, c + 1).value or "").strip()
                    if next_val:
                        return next_val.split("(")[0].strip().replace(" ", "_")

        sn = sheet_name.strip()
        if sn.lower() not in [
            "order",
            "order form",
            "sheet1",
            "sheet",
            "kung",
            "chaothai",
            "new order",
        ]:
            return sn.replace(" ", "_")

        base = os.path.splitext(os.path.basename(self.file_path))[0]
        if "_" in base:
            parts = base.split("_", 1)
            if len(parts[0]) == 36:  # Upload UUID prefix
                base = parts[1]
        for rem in ["Single_Customer_Order_Template", "Order", "Template", "(JCT)", "FULL"]:
            base = base.replace(rem, "").strip()
        base = re.sub(r"[^\w\s-]", "", base).strip().replace(" ", "_")
        return base if base else "SINGLE-CUSTOMER"

    def _detect_sheet_type_and_columns(self, ws, max_scan_rows: int = 12) -> dict[str, Any]:
        """
        Determines whether the worksheet is:
        1. A single-client tabular order sheet (contains an 'Order' / 'Quantity' column header).
        2. A multi-client horizontal matrix (has sub-customer columns after packing).
        """
        if ws.max_row is None or ws.max_column is None:
            ws.calculate_dimension(force=True)

        for r in range(1, min(max_scan_rows + 1, (ws.max_row or 1) + 1)):
            row_vals = [
                str(ws.cell(r, c).value or "").strip()
                for c in range(1, min(30, (ws.max_column or 1) + 1))
            ]
            lower_vals = [v.lower() for v in row_vals]

            order_col = None
            for c_idx, val in enumerate(lower_vals):
                if val in [
                    "order",
                    "order quantity",
                    "quantity",
                    "qty",
                    "bestilling",
                    "จำนวน",
                    "ham 1",
                    "amont to order",
                    "amount to order",
                    "agrisen (kg)",
                ]:
                    order_col = c_idx + 1
                    break

            if order_col is not None:
                code_col = None
                name_en_col = None
                name_vi_col = None
                name_th_col = None
                pack_col = None
                unit_col = None
                pack_weight_col = None

                for c_idx, val in enumerate(lower_vals):
                    col_num = c_idx + 1
                    if val in [
                        "code",
                        "code no",
                        "item code",
                        "product code",
                        "product codes",
                        "รหัส",
                        "t code",
                        "t-code",
                        "t_code",
                    ]:
                        if code_col is None:
                            code_col = col_num
                    elif any(
                        kw in val
                        for kw in ["product name", "english description", "name eng", "english"]
                    ):
                        name_en_col = col_num
                    elif any(
                        kw in val for kw in ["viet", "vietnamese", "name viet", "viet description"]
                    ):
                        name_vi_col = col_num
                    elif any(kw in val for kw in ["thai", "name thai", "รายการ"]):
                        name_th_col = col_num
                    elif any(kw in val for kw in ["description", "item description"]):
                        if name_en_col is None:
                            name_en_col = col_num
                    elif val in {"unit", "หน่วย", "weight"}:
                        unit_col = col_num
                    elif val in {"pack weight kg", "pack weight (kg)"}:
                        pack_weight_col = col_num
                    elif pack_col is None and any(
                        kw in val
                        for kw in ["packing", "pack size", "packge", "pack", "quy cách", "ขนาดแพค"]
                    ):
                        pack_col = col_num

                if code_col is None:
                    if lower_vals and lower_vals[0] in ["no", "no.", "ลำดับ", "#"]:
                        code_col = 2
                    else:
                        code_col = 1

                if name_en_col is None and name_vi_col is None and name_th_col is None:
                    name_en_col = 2 if code_col == 1 else 3

                return {
                    "is_tabular": True,
                    "header_row": r,
                    "order_col": order_col,
                    "code_col": code_col,
                    "name_en_col": name_en_col,
                    "name_vi_col": name_vi_col,
                    "name_th_col": name_th_col,
                    "pack_col": pack_col,
                    "unit_col": unit_col,
                    "pack_weight_col": pack_weight_col,
                }

        hr, pc = self._find_header_anchor(ws, max_scan_rows)
        return {
            "is_tabular": False,
            "header_row": hr,
            "packing_col": pc,
        }

    def _find_header_anchor(self, ws, max_scan_rows: int = 8) -> tuple[int | None, int | None]:
        """
        Dynamically finds header row and packing delimiter column by scanning
        for produce packing keywords ('packing', 'quy cách', 'pack', 'quycach').
        Falls back to detecting customer columns on rows 2-4 if packing label is omitted.
        """
        if ws.max_row is None or ws.max_column is None:
            ws.calculate_dimension(force=True)

        for r in range(1, min(max_scan_rows + 1, ws.max_row + 1)):
            row_labels = [
                str(ws.cell(r, c).value or "").strip().lower()
                for c in range(1, min(12, ws.max_column + 1))
            ]
            has_code = any(
                label in {"code", "product code", "t code", "t-code"} for label in row_labels
            )
            has_name = any(
                "description" in label or "product name" in label for label in row_labels
            )
            for c in range(1, min(12, ws.max_column + 1)):
                cell_val = str(ws.cell(r, c).value or "").strip().lower()
                if (
                    has_code
                    and has_name
                    and any(
                        kw in cell_val for kw in ["packing", "quy cách", "quycach", "pack", "spec"]
                    )
                ):
                    return r, c

        # Fallback for synthetic/legacy sheets where packing header is omitted
        for r in [3, 2, 4]:
            if r <= ws.max_row:
                for c in range(5, min(15, ws.max_column + 1)):
                    cval = ws.cell(r, c).value
                    if cval and str(cval).strip() and not str(cval).startswith("#"):
                        return r, c - 1
        return None, None

    def _detect_group_for_client(self, client_name: str, candidate_group: str) -> str:
        """
        Resolves the route group name based on column group header,
        client code prefix (e.g. 'CT-', 'BE-', 'PTT-'), or sheet context.
        """
        clean_name = client_name.strip()
        # 1. Prefix-based resolution
        if "-" in clean_name:
            prefix = clean_name.split("-")[0].strip().upper()
            if prefix in ["CT", "BE", "PTT", "TA", "W", "PG", "SAPA"]:
                return f"GROUP {prefix}"

        # 2. Candidate group string cleaning
        if candidate_group:
            cg = candidate_group.strip()
            if cg and not cg.startswith("#") and not cg.replace(".", "").isdigit():
                if not cg.upper().startswith("GROUP") and cg.upper() not in ["SHEET1"]:
                    return f"GROUP {cg.upper()}"
                return cg.upper()

        return "GENERAL"

    def parse_all_clients(self) -> dict[str, Any]:
        wb = openpyxl.load_workbook(self.file_path, data_only=True)
        results = {
            "sheets_processed": [],
            "total_clients_found": 0,
            "clients_data": {},  # client_code -> list of items
            "clients_groups": {},  # client_code -> group_name
            "group_summary": {},  # group_name -> shop_count
            "products_catalog": {},  # code -> info
        }

        client_code_from_info = self._get_client_code_from_order_info(wb)
        tabular_sheets = []

        for sheet_name in wb.sheetnames:
            if sheet_name.lower() in ["order info", "instructions", "note", "notes", "blad1"]:
                continue
            ws = wb[sheet_name]
            info = self._detect_sheet_type_and_columns(ws)
            if info["is_tabular"]:
                tabular_sheets.append((sheet_name, ws, info))

        # 1. Tabular sheets branch (e.g. Single Customer Order Template, Chaothai, Hungfat)
        if tabular_sheets:
            for sheet_name, ws, info in tabular_sheets:
                results["sheets_processed"].append(sheet_name)
                client_code = client_code_from_info or self._detect_client_from_sheet(
                    ws, sheet_name
                )
                route_group = self._detect_group_for_client(client_code, "GENERAL")

                if client_code not in results["clients_data"]:
                    results["clients_data"][client_code] = []
                    results["clients_groups"][client_code] = route_group
                    results["group_summary"][route_group] = 1

                hr = info["header_row"]
                code_col = info["code_col"]
                name_en_col = info["name_en_col"]
                name_vi_col = info["name_vi_col"]
                name_th_col = info["name_th_col"]
                pack_col = info["pack_col"]
                order_col = info["order_col"]

                # Check if file has any positive quantities
                has_positive_qty = False
                for row in range(hr + 1, (ws.max_row or hr) + 1):
                    val = ws.cell(row, order_col).value
                    if val is not None and str(val).strip() not in ["", "0", "-", "None"]:
                        try:
                            if float(str(val).replace(",", ".")) > 0:
                                has_positive_qty = True
                                break
                        except ValueError:
                            pass

                for row in range(hr + 1, (ws.max_row or hr) + 1):
                    qty_val = ws.cell(row, order_col).value
                    qty = 0.0
                    if qty_val is not None and str(qty_val).strip() not in ["", "0", "-", "None"]:
                        try:
                            qty = float(str(qty_val).replace(",", "."))
                        except ValueError:
                            qty = 0.0

                    # If file has non-zero quantities, only include rows with positive order quantity!
                    # If file has 0 quantities across all rows (fresh unedited template), take first 10 items with qty=1
                    if has_positive_qty and qty <= 0:
                        continue
                    if not has_positive_qty or qty <= 0:
                        continue

                    raw_code = ws.cell(row, code_col).value if code_col else None
                    raw_en = ws.cell(row, name_en_col).value if name_en_col else None
                    raw_vi = ws.cell(row, name_vi_col).value if name_vi_col else None
                    raw_th = ws.cell(row, name_th_col).value if name_th_col else None
                    raw_pack = ws.cell(row, pack_col).value if pack_col else None

                    if not raw_code and not raw_en and not raw_vi and not raw_th:
                        continue

                    clean_code = str(raw_code).strip() if raw_code else ""
                    if clean_code.endswith(".0"):
                        clean_code = clean_code[:-2]
                    clean_en = str(raw_en).strip() if raw_en else ""
                    clean_vi = str(raw_vi).strip() if raw_vi else ""
                    clean_th = str(raw_th).strip() if raw_th else ""
                    raw_unit = (
                        ws.cell(row, info["unit_col"]).value
                        if info["unit_col"]
                        else (
                            "kg"
                            if "(kg)" in str(ws.cell(hr, order_col).value).lower()
                            else "unknown"
                        )
                    )
                    numeric_unit = (
                        "kg"
                        if re.search(r"(?:^|_)50H(?:\.|\s)", os.path.basename(self.file_path), re.I)
                        else "g"
                        if info["unit_col"]
                        and str(ws.cell(hr, info["unit_col"]).value).lower() == "weight"
                        else None
                    )
                    adjacent = (
                        ws.cell(row, info["pack_weight_col"]).value
                        if info["pack_weight_col"]
                        else None
                    )
                    clean_pack, issues = normalize_pack(
                        raw_pack, numeric_unit=numeric_unit, adjacent_kg=adjacent
                    )

                    item_key = clean_code or clean_en or clean_vi
                    if item_key not in results["products_catalog"]:
                        results["products_catalog"][item_key] = {
                            "code": clean_code,
                            "name_vi": clean_vi,
                            "name_en": clean_en,
                            "name_th": clean_th,
                            "pack_size": clean_pack,
                        }

                    results["clients_data"][client_code].append(
                        {
                            "sheet": sheet_name,
                            "group": route_group,
                            "t_code": clean_code,
                            "name_vi": clean_vi,
                            "name_en": clean_en,
                            "name_th": clean_th,
                            "pack_size": clean_pack,
                            "quantity": qty,
                            "unit": unit_key(str(raw_unit or "unknown")),
                            "quantity_issues": issues,
                            "source": {
                                "sheet": sheet_name,
                                "row": row,
                                "raw_pack_size": raw_pack,
                                "raw_quantity": qty_val,
                                "raw_unit": raw_unit,
                                "adjacent_pack_kg": adjacent,
                            },
                        }
                    )

            results["total_clients_found"] = len(results["clients_data"])
            wb.close()
            return results

        # 2. Horizontal Matrix branch (e.g. Order Tonio, NGA matrix)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            hr, pc = self._find_header_anchor(ws)
            if not hr or not pc:
                continue

            results["sheets_processed"].append(sheet_name)

            # Discover client columns starting at pc + 1
            client_cols = {}
            current_group = sheet_name.strip().upper()

            for col in range(pc + 1, ws.max_column + 1):
                cell_val = ws.cell(hr, col).value
                if not cell_val or not str(cell_val).strip() or str(cell_val).startswith("#"):
                    continue
                client_code = str(cell_val).strip()
                if client_code.lower() in [
                    "total",
                    "tổng",
                    "tổng cộng",
                    "order",
                    "unit",
                    "notes",
                    "action",
                ]:
                    continue

                # Check row above header for group declaration
                if hr > 1:
                    r_above = ws.cell(hr - 1, col).value
                    if r_above and str(r_above).strip():
                        grp_str = str(r_above).strip()
                        if not grp_str.replace(".", "").isdigit() and not grp_str.startswith("#"):
                            current_group = grp_str

                resolved_group = self._detect_group_for_client(client_code, current_group)
                client_cols[col] = (client_code, resolved_group)

                if client_code not in results["clients_data"]:
                    results["clients_data"][client_code] = []
                    results["clients_groups"][client_code] = resolved_group
                    results["group_summary"][resolved_group] = (
                        results["group_summary"].get(resolved_group, 0) + 1
                    )

            # Extract product rows from hr + 1 onwards
            for row in range(hr + 1, ws.max_row + 1):
                t_code = ws.cell(row, 1).value
                name_vi = ws.cell(row, 2).value
                name_en = ws.cell(row, 3).value
                name_th = ws.cell(row, 4).value if pc > 4 else None
                pack_size = ws.cell(row, pc).value

                if not t_code and not name_vi and not name_en and not name_th:
                    continue

                clean_code = str(t_code).strip() if t_code else ""
                clean_vi = str(name_vi).strip() if name_vi else ""
                clean_en = str(name_en).strip() if name_en else ""
                clean_th = str(name_th).strip() if name_th else ""
                clean_pack, pack_issues = normalize_pack(pack_size)
                declared_unit = "unknown"
                # A matrix has no inherent quantity unit. An explicit template field
                # or the intake review supplies it; customer country never does.
                if "Order Info" in wb.sheetnames:
                    for k, v, *_ in wb["Order Info"].iter_rows(
                        min_row=1, max_col=2, values_only=True
                    ):
                        if str(k or "").strip().casefold() == "quantity unit":
                            declared_unit = unit_key(str(v or "unknown"))

                combined_check = f"{clean_code} {clean_vi} {clean_en}".lower()
                if any(w in combined_check for w in ["total", "tổng", "#ref", "#name"]):
                    continue

                if clean_code and clean_code not in results["products_catalog"]:
                    results["products_catalog"][clean_code] = {
                        "code": clean_code,
                        "name_vi": clean_vi,
                        "name_en": clean_en,
                        "name_th": clean_th,
                        "pack_size": clean_pack,
                    }

                # Check quantities for each discovered client
                for col, (client_code, grp) in client_cols.items():
                    qty_val = ws.cell(row, col).value
                    if qty_val is not None:
                        try:
                            qty = float(qty_val)
                            if qty > 0:
                                results["clients_data"][client_code].append(
                                    {
                                        "sheet": sheet_name,
                                        "group": grp,
                                        "t_code": clean_code,
                                        "name_vi": clean_vi,
                                        "name_en": clean_en,
                                        "name_th": clean_th,
                                        "pack_size": clean_pack,
                                        "quantity": qty,
                                        "unit": declared_unit,
                                        "quantity_issues": pack_issues,
                                        "source": {
                                            "sheet": sheet_name,
                                            "row": row,
                                            "column": col,
                                            "raw_pack_size": pack_size,
                                            "raw_quantity": qty_val,
                                            "raw_unit": declared_unit,
                                        },
                                    }
                                )
                        except (ValueError, TypeError):
                            pass

        results["total_clients_found"] = len(results["clients_data"])
        wb.close()
        return results

    def inspect_sub_customers(self) -> dict[str, Any]:
        """
        Fast lightweight inspection to immediately discover sub-customers on file upload.
        Enables instant Intake & Acknowledgment for human verification with Group breakdown.
        Seamlessly handles both multi-client horizontal matrices and single-client tabular sheets.
        """
        wb = openpyxl.load_workbook(self.file_path, read_only=True, data_only=True)
        sheets_processed = []
        unique_clients = {}
        group_summary = {}

        client_code_from_info = self._get_client_code_from_order_info(wb)
        tabular_sheets = []

        for sheet_name in wb.sheetnames:
            if sheet_name.lower() in ["order info", "instructions", "note", "notes", "blad1"]:
                continue
            ws = wb[sheet_name]
            info = self._detect_sheet_type_and_columns(ws)
            if info["is_tabular"]:
                tabular_sheets.append((sheet_name, ws, info))

        # 1. Tabular sheets branch
        if tabular_sheets:
            for sheet_name, ws, info in tabular_sheets:
                client_code = client_code_from_info or self._detect_client_from_sheet(
                    ws, sheet_name
                )
                route_group = self._detect_group_for_client(client_code, "GENERAL")

                ordered_count = 0
                order_col = info["order_col"]
                hr = info["header_row"]
                for r in ws.iter_rows(min_row=hr + 1, values_only=True):
                    if r and len(r) >= order_col:
                        val = r[order_col - 1]
                        if val is not None and str(val).strip() not in ["", "0", "-", "None"]:
                            try:
                                if float(str(val).replace(",", ".")) > 0:
                                    ordered_count += 1
                            except ValueError:
                                pass

                if client_code not in unique_clients:
                    unique_clients[client_code] = {
                        "client_code": client_code,
                        "route_group": route_group,
                        "sheets": [sheet_name],
                    }
                    group_summary[route_group] = group_summary.get(route_group, 0) + 1
                elif sheet_name not in unique_clients[client_code]["sheets"]:
                    unique_clients[client_code]["sheets"].append(sheet_name)

                sheets_processed.append(
                    {
                        "sheet_name": sheet_name,
                        "sub_customer_count": 1,
                        "items_count": ordered_count,
                    }
                )

            wb.close()
            return {
                "sheets_processed": sheets_processed,
                "total_sub_customers": len(unique_clients),
                "group_summary": group_summary,
                "sub_customer_codes": sorted(list(unique_clients.keys())),
                "sub_customer_details": list(unique_clients.values()),
                "status": "ACKNOWLEDGED_PENDING_VERIFICATION",
                "verification_notice": f"Single-customer tabular order detected: {len(unique_clients)} customer account with ordered line items. Please verify before confirming split.",
            }

        # 2. Horizontal Matrix branch
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            hr, pc = self._find_header_anchor(ws)
            if not hr or not pc:
                continue

            current_group = sheet_name.strip().upper()
            client_count_in_sheet = 0

            # Read rows up to header row
            rows = list(ws.iter_rows(min_row=max(1, hr - 1), max_row=hr, values_only=True))
            if hr > 1 and len(rows) >= 2:
                r_above, r_hdr = rows[0], rows[1]
            else:
                r_above, r_hdr = None, rows[0]

            for col_idx in range(pc, len(r_hdr)):
                cell_val = r_hdr[col_idx]
                if not cell_val or not str(cell_val).strip() or str(cell_val).startswith("#"):
                    continue
                client_code = str(cell_val).strip()
                if client_code.lower() in [
                    "total",
                    "tổng",
                    "tổng cộng",
                    "order",
                    "unit",
                    "notes",
                    "action",
                ]:
                    continue

                if r_above and col_idx < len(r_above) and r_above[col_idx]:
                    grp_val = str(r_above[col_idx]).strip()
                    if (
                        grp_val
                        and not grp_val.startswith("#")
                        and not grp_val.replace(".", "").isdigit()
                    ):
                        current_group = grp_val

                resolved_group = self._detect_group_for_client(client_code, current_group)
                client_count_in_sheet += 1

                if resolved_group not in group_summary:
                    group_summary[resolved_group] = 0

                if client_code not in unique_clients:
                    unique_clients[client_code] = {
                        "client_code": client_code,
                        "route_group": resolved_group,
                        "sheets": [sheet_name],
                    }
                    group_summary[resolved_group] += 1
                elif sheet_name not in unique_clients[client_code]["sheets"]:
                    unique_clients[client_code]["sheets"].append(sheet_name)

            if client_count_in_sheet > 0:
                sheets_processed.append(
                    {"sheet_name": sheet_name, "sub_customer_count": client_count_in_sheet}
                )

        wb.close()
        return {
            "sheets_processed": sheets_processed,
            "total_sub_customers": len(unique_clients),
            "group_summary": group_summary,
            "sub_customer_codes": sorted(list(unique_clients.keys())),
            "sub_customer_details": list(unique_clients.values()),
            "status": "ACKNOWLEDGED_PENDING_VERIFICATION",
            "verification_notice": f"Found {len(unique_clients)} sub-customers across {len(sheets_processed)} sheets. Please verify before confirming split.",
        }
