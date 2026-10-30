import json

dump_path = r'D:\Autonoetic_edge\Sunny thailand\Box-agriculture project\documents\new input format\analysis_dump.json'
with open(dump_path, encoding='utf-8') as f:
    data = json.load(f)

print(f"Total files: {len(data)}")
for item in data:
    idx = item['index']
    name = item['filename']
    ext = item['extension']
    size = item['size_kb']
    if ext in ['.xlsx', '.xls']:
        sheets = list(item.get('sheets', {}).keys())
        rows_info = [f"{s}: {item['sheets'][s]['non_empty_rows']}r x {item['sheets'][s]['max_column']}c" for s in sheets]
        print(f"[{idx:02d}] {name} ({size} KB) | Sheets: {', '.join(rows_info)}")
    elif ext == '.pdf':
        print(f"[{idx:02d}] {name} ({size} KB) | PDF Pages: {item.get('page_count')}")
    elif ext in ['.png', '.jpg', '.jpeg']:
        print(f"[{idx:02d}] {name} ({size} KB) | Image: {item.get('image_size')} ({item.get('image_format')})")

