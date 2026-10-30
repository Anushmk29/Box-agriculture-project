"""
Reset Operational / Transactional Data
Cleans all orders, purchasing lists, boxes, invoices, receipts, manifests, and audit trails
while keeping Master Data (Users, Box Catalogue, Produce SKUs, Customers, and Price Lists) pristine.
"""

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from dotenv import load_dotenv

load_dotenv(os.path.join(backend_dir, ".env"))

from sqlalchemy import text

from app.core.database import SessionLocal

TRANSACTIONAL_TABLES = [
    "receipts",
    "payment_transactions",
    "invoice_line_items",
    "invoices",
    "box_items",
    "boxes",
    "transportation_manifests",
    "purchasing_order_sources",
    "purchasing_items",
    "master_purchasing_lists",
    "client_order_items",
    "client_orders",
    "raw_order_files",
    "agent_handoffs",
    "audit_logs",
]

MASTER_TABLES = [
    "users",
    "box_catalogue",
    "products",
    "customers",
    "price_lists",
    "customer_selling_prices",
    "client_sku_mappings",
]


def clear_operational_data():
    db = SessionLocal()
    try:
        print("=" * 70)
        print("RESETTING OPERATIONAL & TRANSACTIONAL DATA (FRESH START)")
        print("=" * 70)

        joined_tables = ", ".join(TRANSACTIONAL_TABLES)
        print(f"Truncating operational tables in Supabase: {joined_tables}...")
        db.execute(text(f"TRUNCATE TABLE {joined_tables} CASCADE;"))
        db.commit()

        print("\n--- Operational Tables Status (Must be 0) ---")
        all_clean = True
        for table in TRANSACTIONAL_TABLES:
            cnt = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            status = "✓ Clean (0)" if cnt == 0 else f"✗ ERROR: {cnt} rows remain"
            print(f"  • {table:<28}: {status}")
            if cnt != 0:
                all_clean = False

        print("\n--- Master Data Integrity Check ---")
        for table in MASTER_TABLES:
            cnt = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  • {table:<28}: {cnt} rows preserved")

        if all_clean:
            print("\n" + "=" * 70)
            print("SUCCESS: System reset complete! All operational queues are empty.")
            print("You are ready to test Phase 1 through Phase 4 from a clean slate.")
            print("=" * 70)
        else:
            print("\nWARNING: Some tables could not be cleared completely.")

    finally:
        db.close()


if __name__ == "__main__":
    clear_operational_data()
