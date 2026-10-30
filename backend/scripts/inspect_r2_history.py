"""Inspect an explicitly configured Supabase review target without writing data."""

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="SUPABASE_STAGING_DATABASE_URL")
    parser.add_argument("--row-limit", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get(args.database_url_env)
    if not url:
        parser.error(f"Set {args.database_url_env}; this utility never falls back to the application's database")
    if not 1 <= args.row_limit <= 100000:
        parser.error("row-limit must be between 1 and 100000")

    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from app.core.database_urls import normalize_database_url
    from app.modules.orders.history_review import review_records

    queries = {
        "price_lists": "SELECT id,product_id,customer_id,pack_size,valid_from,valid_to FROM price_lists ORDER BY id",
        "customer_selling_prices": "SELECT id,product_id,main_customer_id,sub_customer_id,pack_size,valid_from,valid_to FROM customer_selling_prices ORDER BY id",
        "raw_order_files": "SELECT id,shipment_target_date FROM raw_order_files ORDER BY id",
        "customer_order_context": "SELECT o.id,o.customer_id,o.main_customer_id,c.parent_customer_id FROM client_orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id",
        "invalid_main_accounts": "SELECT r.id FROM raw_order_files r LEFT JOIN customers c ON c.id=r.main_customer_id WHERE r.main_customer_id IS NOT NULL AND (c.id IS NULL OR NOT c.is_main_customer) ORDER BY r.id",
    }
    engine = None
    try:
        engine = create_engine(normalize_database_url(url), poolclass=NullPool,
                               connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                tables, truncated = {}, []
                for name, query in queries.items():
                    rows = [dict(row) for row in connection.execute(
                        text(query + " LIMIT :limit"), {"limit": args.row_limit + 1}
                    ).mappings()]
                    if len(rows) > args.row_limit:
                        truncated.append(name)
                    tables[name] = rows[:args.row_limit]
                report = review_records(tables)
                report.update(rows_reviewed={name: len(rows) for name, rows in tables.items()},
                              truncated_tables=truncated, complete=not truncated and not report["issues_truncated"],
                              transaction_read_only=True, platform="Supabase Database")
            finally:
                transaction.rollback()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "complete": report["complete"],
                          "review_candidates": report["issue_count"]}))
        return 0
    except Exception as exc:
        # Driver messages may include connection credentials; never print them.
        print(json.dumps({"status": "failed", "exception_type": type(exc).__name__}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
