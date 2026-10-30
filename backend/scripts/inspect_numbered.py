import os

import openpyxl

folder = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format'

numbered_files = [
    '19T.xlsx', '20T.xlsx', '40T.xlsx', '412T.xlsx',
    '50H.xlsx', '66H.xlsx', '68H.xlsx', '76H.xlsx', '77H.xlsx',
    '87L.xlsx', '928L.xlsx', '931L (1).xlsx'
]

for fname in numbered_files:
    path = os.path.join(folder, fname)
    wb = openpyxl.load_workbook(path, data_only=True)
    sheet = wb.sheetnames[0]
    ws = wb[sheet]
    print("\n=======================================================")
    print(f"=== {fname} | Sheet: {sheet} | max_r={ws.max_row}, max_c={ws.max_column} ===")
    
    # scan rows
    non_empty = []
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
        vals = [str(c).strip() if c is not None else '' for c in row]
        if any(v != '' for v in vals):
            non_empty.append((r_idx, vals))
            
    print(f"  Non-empty rows: {len(non_empty)}")
    # Header area
    for r_idx, vals in non_empty[:5]:
        line_preview = [f"c{c_idx}:{v}" for c_idx, v in enumerate(vals) if v != ''][:8]
        print(f"  [Header L{r_idx}] {' | '.join(line_preview)}")
        
    # Look for actual orders (rows with non-empty values beyond item description)
    # Let's inspect column headers around row 1-5 to see where quantity is
    order_rows = []
    for r_idx, vals in non_empty[5:]:
        # check if there's any non-zero, non-blank quantity
        vals_after_col3 = [v for v in vals[3:] if v != '' and v != '0' and v != 'None']
        if vals_after_col3:
            order_rows.append((r_idx, [f"c{c_idx}:{v}" for c_idx, v in enumerate(vals) if v != '']))
            
    print(f"  Found {len(order_rows)} rows with non-empty order data")
    for r_idx, o in order_rows[:4]:
        print(f"    Item L{r_idx}: {' | '.join(o[:6])}")

