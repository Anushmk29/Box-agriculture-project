"""Build immutable packing evidence and an exception report; never connects to a database."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.modules.packing.history_import import extract_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "documents/new docs-mail")
    parser.add_argument("--output", type=Path, default=ROOT / "backend/app/assets/packing_history.json")
    parser.add_argument("--report", type=Path, default=ROOT / "outputs/packing-history/REPORT.md")
    args = parser.parse_args()
    result = extract_corpus(args.source.resolve(strict=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Packing history extraction", "", "Read-only extraction. No capacity or template has been approved.", ""]
    lines += [f"- {key.replace('_', ' ')}: {value}" for key, value in result["summary"].items()]
    lines += ["", "Source exceptions remain visible in Products & boxes → Packing history.",
              "KG lines need physical pack counts; PIECES lines need verified net kilograms.",
              "Register trailing numbers are not treated as pack counts.",
              "Order summaries are inventoried; shipment-total reconciliation still requires matching order scope.", ""]
    for example in result["examples"]:
        issues = [*example["issues"], *(f"Line {item['line']}: {issue}" for item in example["items"] for issue in item["issues"])]
        if issues:
            lines += [f"## {example['shipment']} / {example['subcustomer']} / box {example['box_number']}",
                      f"Source: {example['source']}, pages {example['pages']}", *[f"- {issue}" for issue in issues], ""]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
