"""Explicit-target source maintenance; dry-run by default, never app-URL fallback."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from app.core.database_urls import normalize_database_url
from app.core.source_backup import verify_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["migrate", "gc", "export", "verify", "restore"])
    parser.add_argument("--database-url", default=os.getenv("BOX_SOURCE_DATABASE_URL"))
    parser.add_argument("--project-ref", help="Must match the explicitly selected Supabase database and Storage URL")
    parser.add_argument("--raw-file-id")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--after-id", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and args.operation in {"verify", "export"}:
        parser.error("verify/export are read-only and do not accept --apply")
    if args.operation == "verify":
        if not args.directory:
            parser.error("verify requires --directory")
        print(json.dumps(verify_backup(args.directory)))
        return
    if not args.database_url or not args.project_ref:
        parser.error("Select an explicit database URL and project reference; there is no application database fallback")
    from app.core.config import settings
    from app.modules.orders.source_lifecycle import (
        export_sources,
        migrate_legacy_source,
        reconcile_orphans,
        restore_sources,
    )
    url = make_url(normalize_database_url(args.database_url))
    reference = ((url.username or "").removeprefix("postgres.") if (url.username or "").startswith("postgres.")
                 else (url.host or "").removeprefix("db.").removesuffix(".supabase.co"))
    if reference != args.project_ref or settings.SUPABASE_URL.rstrip("/") != f"https://{args.project_ref}.supabase.co":
        parser.error("Database target, project reference and Storage API project must match")
    if settings.FILE_STORAGE_PROVIDER != "supabase":
        parser.error("Maintenance targets private Supabase Storage, not the development adapter")
    if args.operation in {"export", "restore"} and not args.directory:
        parser.error("export/restore requires --directory")
    if args.operation == "migrate" and not args.raw_file_id:
        parser.error("migrate requires one explicit --raw-file-id")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as db:
            # Reference snapshots and maintenance locks are transaction scoped.
            if not args.apply:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            if set(db.scalars(text("SELECT version_num FROM alembic_version"))) != {"20261027_0023"}:
                raise ValueError("Use the matching source/schema release before document maintenance")
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(text("SET LOCAL statement_timeout = '30s'"))
            if args.operation == "migrate":
                result = migrate_legacy_source(db, args.raw_file_id, apply=args.apply)
            elif args.operation == "gc":
                if not args.apply:
                    # GC safety depends on fresh committed references after acquiring
                    # each lock; repeatable-read snapshots are inappropriate here.
                    db.rollback()
                    db.execute(text("SET TRANSACTION READ ONLY"))
                    db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    db.execute(text("SET LOCAL statement_timeout = '30s'"))
                result = reconcile_orphans(db, apply=args.apply, offset=args.offset, limit=args.limit)
            elif args.operation == "export":
                result = export_sources(db, args.directory, after_id=args.after_id, limit=args.limit)
            else:
                result = restore_sources(db, args.directory, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
            print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider/driver exceptions can contain credentials or business content.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "action": "Review the selected target and source inventory; no automatic retry was performed"}))
        raise SystemExit(1) from None
