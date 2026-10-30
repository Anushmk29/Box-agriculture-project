"""Import reviewed mail-document billing customers; preview unless --apply.

Run from backend: .venv/Scripts/python.exe scripts/import_order_customers.py --apply
Uses the application's configured database and never overwrites existing accounts.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.services.order_customer_reference import import_reference_customers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit new accounts (default: preview and roll back)")
    args = parser.parse_args()
    with SessionLocal() as db:
        for row in import_reference_customers(db):
            print(f"{row['action']}: {row['code']} | {row['name']} | {row['route']}")
        if args.apply:
            db.commit()
            print("Customer import saved.")
        else:
            db.rollback()
            print("Preview only; no changes saved. Add --apply to save.")


if __name__ == "__main__":
    main()
