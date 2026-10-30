import os

import openpyxl

folder = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format'
for fname in ['19T.xlsx', '20T.xlsx', '40T.xlsx', '412T.xlsx', '50H.xlsx']:
    path = os.path.join(folder, fname)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    print(f"\n=== {fname} | Sheet: {wb.sheetnames[0]} ===")
    rows = list(ws.iter_rows(values_only=True))
    for r_idx, row in enumerate(rows[:10], 1):
        non_empty = [f"c{idx}:{v}" for idx, v in enumerate(row) if v is not None and str(v).strip() != '']
        if non_empty:
            print(f"  L{r_idx}: {' | '.join(non_empty[:8])}")
            
    # Sample ordered rows
    order_items = []
    for r_idx, row in enumerate(rows[5:], 6):
        vals = [str(v).strip() for v in row if v is not None]
        # check if row has numeric values or items
        if any(v.replace('.', '', 1).isdigit() for v in vals):
            non_empty = [f"c{idx}:{v}" for idx, v in enumerate(row) if v is not None and str(v).strip() != '']
            if len(non_empty) >= 4:
                order_items.append((r_idx, non_empty))
    print(f"  Total items with order data: {len(order_items)}")
    for r_idx, items in order_items[:3]:
        print(f"    Item L{r_idx}: {' | '.join(items[:6])}")

