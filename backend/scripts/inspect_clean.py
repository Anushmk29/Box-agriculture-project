import os

import openpyxl

folder = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format'

def inspect_clean(fname, sheet_name=None):
    p = os.path.join(folder, fname)
    wb = openpyxl.load_workbook(p, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active
    print("\n=======================================================")
    print(f"=== {fname} | Sheet: {ws.title} ===")
    rows = list(ws.iter_rows(values_only=True))
    for r_idx, r in enumerate(rows[:20], 1):
        clean = [f"c{c_idx}:{str(c)[:30].strip()}" for c_idx, c in enumerate(r) if c is not None and str(c).strip() != '']
        if clean:
            print(f"  L{r_idx}: {' | '.join(clean[:8])}")

inspect_clean('Order Hungfat 2026-W32.xlsx', 'New Order')
inspect_clean('Order PINTO THAIAHMED ALMAN(MLD) 05-09-2026.xlsx', 'KUNG')
inspect_clean('Order (JCT) CHAOTHAI CO.,LTD 29-08-2026 .xlsx', 'CHAOTHAI')
inspect_clean('Ly-Thanh-Sarl Order Vegetable for Monday 07-09-2026.xlsx', 'Order form')

