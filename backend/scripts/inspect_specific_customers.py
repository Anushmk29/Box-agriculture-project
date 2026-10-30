import os

import openpyxl
import xlrd

folder = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format'

def inspect_hf():
    p = os.path.join(folder, 'Order Hungfat 2026-W32.xlsx')
    wb = openpyxl.load_workbook(p, data_only=True)
    ws = wb['New Order']
    print("=== Order Hungfat 2026-W32.xlsx ===")
    for r in range(1, 12):
        row = [str(c) if c is not None else '' for c in ws.iter_rows(values_only=True)][r-1]
        print(f"  L{r}: {[c for c in row if c][:8]}")

def inspect_pinto():
    p = os.path.join(folder, 'Order PINTO THAIAHMED ALMAN(MLD) 05-09-2026.xlsx')
    wb = openpyxl.load_workbook(p, data_only=True)
    ws = wb['KUNG']
    print("\n=== Order PINTO THAIAHMED ALMAN(MLD) 05-09-2026.xlsx ===")
    for r in range(1, 15):
        row = [str(c) if c is not None else '' for c in ws.iter_rows(values_only=True)][r-1]
        print(f"  L{r}: {[c for c in row if c][:8]}")

def inspect_jct():
    p = os.path.join(folder, 'Order (JCT) CHAOTHAI CO.,LTD 29-08-2026 .xlsx')
    wb = openpyxl.load_workbook(p, data_only=True)
    ws = wb['CHAOTHAI']
    print("\n=== Order (JCT) CHAOTHAI CO.,LTD 29-08-2026 .xlsx ===")
    for r in range(1, 15):
        row = [str(c) if c is not None else '' for c in ws.iter_rows(values_only=True)][r-1]
        print(f"  L{r}: {[c for c in row if c][:8]}")

def inspect_ly_thanh():
    for f in ['Ly-Thanh-Sarl Order Vegetable for Monday 07-09-2026.xlsx', 
              'FRIBOURG_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026 FINAL.xls',
              'VEVEY_Ly-Thanh-Sarl-Order-Vegetable-for-07.09.2026.xls']:
        p = os.path.join(folder, f)
        print(f"\n=== {f} ===")
        if f.endswith('.xlsx'):
            wb = openpyxl.load_workbook(p, data_only=True)
            ws = wb.active
            for r in range(7, 16):
                row = [str(c) if c is not None else '' for c in ws.iter_rows(values_only=True)][r-1]
                print(f"  L{r}: {[c for c in row if c][:8]}")
        else:
            wb = xlrd.open_workbook(p)
            ws = wb.sheet_by_index(0)
            for r in range(5, 14):
                row = [str(c) if c != '' else '' for c in ws.row_values(r)]
                print(f"  L{r+1}: {[c for c in row if c][:8]}")

inspect_hf()
inspect_pinto()
inspect_jct()
inspect_ly_thanh()

