import os

import openpyxl
import xlrd

folder = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format'

customer_files = [
    'Order Hungfat 2026-W32.xlsx',
    'Order P-PUI shipment ETD 07.08.2026.xlsx',
    'Order PINTO THAIAHMED ALMAN(MLD) 05-09-2026.xlsx',
    'Order Tonio 07.08.26.xlsx',
    'Order YACHI shipment ETD 07.08.2026.xlsx',
    'Order (JCT) CHAOTHAI CO.,LTD 29-08-2026 .xlsx',
    'Order (JCT1) CHAOTHAI CO.,LTD 29-08-2026 .xlsx',
    'Ly-Thanh-Sarl Order Vegetable for Monday 07-09-2026.xlsx',
    'FRIBOURG_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026 FINAL.xls',
    'VEVEY_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026.xls',
    'ราการขนม NGA 07-09-2026.xlsx',
    'รายการขนม LY 06-09-2026.xlsx'
]

for fname in customer_files:
    ext = os.path.splitext(fname)[1].lower()
    path = os.path.join(folder, fname)
    print("\n=======================================================")
    print(f"=== FILE: {fname} ===")
    
    if ext == '.xlsx':
        wb = openpyxl.load_workbook(path, data_only=True)
        sheets = wb.sheetnames
        print(f"  Sheets ({len(sheets)}): {sheets}")
        for sname in sheets[:3]: # inspect first 3 sheets
            ws = wb[sname]
            print(f"  --- Sheet: {sname} ({ws.max_row} rows x {ws.max_column} cols) ---")
            rows = list(ws.iter_rows(values_only=True))
            # show first 6 non-empty rows
            count = 0
            for r_idx, r in enumerate(rows, 1):
                clean = [f"c{c_idx}:{str(c)[:30].strip()}" for c_idx, c in enumerate(r) if c is not None and str(c).strip() != '']
                if clean:
                    count += 1
                    if count <= 6:
                        print(f"    L{r_idx}: {' | '.join(clean[:8])}")
            print(f"    Total non-empty rows: {count}")
    elif ext == '.xls':
        wb = xlrd.open_workbook(path)
        sheets = wb.sheet_names()
        print(f"  Sheets ({len(sheets)}): {sheets}")
        for sname in sheets[:2]:
            ws = wb.sheet_by_name(sname)
            print(f"  --- Sheet: {sname} ({ws.nrows} rows x {ws.ncols} cols) ---")
            count = 0
            for r_idx in range(ws.nrows):
                r = ws.row_values(r_idx)
                clean = [f"c{c_idx}:{str(c)[:30].strip()}" for c_idx, c in enumerate(r) if c is not None and str(c).strip() != '']
                if clean:
                    count += 1
                    if count <= 6:
                        print(f"    L{r_idx+1}: {' | '.join(clean[:8])}")
            print(f"    Total non-empty rows: {count}")

