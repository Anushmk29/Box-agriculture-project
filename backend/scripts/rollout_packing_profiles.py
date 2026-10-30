"""Back up app data, migrate atomically, and stage unreviewed reference profiles.

Run from backend. Default is read-only; --apply requires the configured project ref.
No profile, product classification or box measurement is approved by this script.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session, selectinload

from alembic import command
from app.core.database import engine
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.modules.packing.research import pending_drafts, stage_drafts


def json_value(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def serialize(value):
    return json.dumps(value, default=json_value, sort_keys=True, ensure_ascii=False)


def locked_history(connection, columns=None):
    inspector = sa.inspect(connection)
    columns = columns or {
        t: [c["name"] for c in inspector.get_columns(t)] for t in ["boxes", "box_items"]
    }
    result = {}
    for table, names in columns.items():
        selected = ", ".join(f't."{name}"' for name in names)
        predicate = (
            "t.is_locked = true"
            if table == "boxes"
            else "t.box_id IN (SELECT id FROM boxes WHERE is_locked = true)"
        )
        result[table] = [
            dict(r)
            for r in connection.execute(
                sa.text(f'SELECT {selected} FROM "{table}" t WHERE {predicate} ORDER BY t.id')
            ).mappings()
        ]
    return hashlib.sha256(serialize(result).encode()).hexdigest(), columns


def snapshot(connection, path):
    inspector = sa.inspect(connection)
    backup = {"tables": {}, "schema": {}}
    for table in inspector.get_table_names(schema="public"):
        if not re.fullmatch(r"[a-z_][a-z_0-9]*", table):
            raise RuntimeError("Unexpected application table identifier")
        backup["tables"][table] = [
            dict(r)
            for r in connection.execute(sa.text(f'SELECT * FROM public."{table}"')).mappings()
        ]
        backup["schema"][table] = {
            "columns": inspector.get_columns(table, schema="public"),
            "foreign_keys": inspector.get_foreign_keys(table, schema="public"),
            "indexes": inspector.get_indexes(table, schema="public"),
            "checks": inspector.get_check_constraints(table, schema="public"),
        }
    encoded = serialize(backup).encode()
    path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    path.with_suffix(".sha256").write_text(digest, encoding="utf-8")
    return {t: len(rows) for t, rows in backup["tables"].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-project-ref")
    args = parser.parse_args()
    url = engine.url
    actual_ref = (url.username or "").split(".")[-1]
    if actual_ref == "postgres":
        actual_ref = (url.host or "").removeprefix("db.").split(".")[0]
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION READ ONLY"))
        version = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        print(
            json.dumps(
                {"project_ref": actual_ref, "current_migration": version, "apply": args.apply}
            )
        )
    if not args.apply:
        return
    if not args.expected_project_ref or args.expected_project_ref != actual_ref:
        raise RuntimeError("Configured database does not match --expected-project-ref")
    backup_dir = (
        ROOT / ".codex-artifact/database-backups" / f"packing-profiles-{uuid.uuid4().hex[:12]}"
    )
    backup_dir.mkdir(parents=True)
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        table_counts = snapshot(connection, backup_dir / "application-data.json")
        history, old_columns = locked_history(connection)
    environment = ROOT / "backend/.env"
    if environment.exists():
        shutil.copy2(environment, backup_dir / "backend.env.before")
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL lock_timeout='10s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout='120s'"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        with Session(bind=connection, expire_on_commit=False) as db:
            products = (
                db.query(Product)
                .filter_by(is_active=True)
                .options(selectinload(Product.packing_profiles))
                .order_by(Product.id)
                .with_for_update()
                .all()
            )
            pending = pending_drafts(products)
            count, affected = len(pending), len({p.id for p, _ in pending})
            estimates = sum(c["evidence"]["estimate_high_cm3"] is not None for _, c in pending)
            stage_drafts(db, pending)
            db.add(
                AuditLog(
                    event_type="PACKING_REFERENCE_DRAFTS_STAGED",
                    actor_id="packing-reference-rollout",
                    actor_role="SYSTEM",
                    target_table="products",
                    target_id="reference-drafts",
                    before_state={"migration": version},
                    after_state={
                        "profiles_added": count,
                        "products": affected,
                        "reference_version": "2026-10-23",
                        "approved_profiles": 0,
                    },
                )
            )
            db.flush()
            assert not pending_drafts(products), "Reference staging must be idempotent"
        after, _ = locked_history(connection, old_columns)
        if after != history:
            raise RuntimeError(
                "Locked packing history changed; migration transaction will be rolled back"
            )
        rls = dict(
            connection.execute(
                sa.text(
                    "SELECT relname,relrowsecurity FROM pg_class WHERE relname IN ('product_pack_profiles','product_box_limits')"
                )
            ).all()
        )
        if not all(rls.get(t) for t in ["product_pack_profiles", "product_box_limits"]):
            raise RuntimeError("Packing reference tables must retain row-level security")
    # Preparation mode keeps existing operations available while staff review profiles.
    contents = environment.read_text(encoding="utf-8") if environment.exists() else ""
    if re.search(r"(?m)^PACKING_PROFILES_ENABLED=.*$", contents):
        contents = re.sub(
            r"(?m)^PACKING_PROFILES_ENABLED=.*$", "PACKING_PROFILES_ENABLED=false", contents
        )
    else:
        contents += "\n# Enable after staff review of packing profiles and box interiors.\nPACKING_PROFILES_ENABLED=false\n"
    environment.write_text(contents, encoding="utf-8")
    with Session(engine) as db:
        ready_products = db.query(Product).filter_by(packing_reviewed=True, is_active=True).count()
        ready_boxes = (
            db.query(BoxCatalogue).filter_by(packing_reviewed=True, is_active=True).count()
        )
    report = {
        "migration": "20261024_0013",
        "profiles_added": count,
        "products_with_added_profiles": affected,
        "profiles_with_volume_comparator": estimates,
        "active_products": len(products),
        "reviewed_classifications": ready_products,
        "reviewed_box_interiors": ready_boxes,
        "locked_history_sha256": history,
        "locked_history_unchanged": True,
        "strict_planning_enabled": False,
        "backup_table_counts": table_counts,
        "new_tables_rls": rls,
    }
    report_dir = ROOT / "outputs/packing-research"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "rollout-result.json").write_text(serialize(report), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "backup_table_counts"}))


if __name__ == "__main__":
    main()
