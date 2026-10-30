"""Preview source reconciliation; back up, migrate and import drafts without changing history."""

import argparse
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session, load_only, selectinload

from alembic import command

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from rollout_packing_profiles import locked_history, snapshot

from app.core.config import settings
from app.core.database import engine
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue, Product
from app.modules.packing.product_table import apply_reconciliation, reconciliation, sources

NEW = {"packing_volume_l_per_kg", "packing_volume_reviewed", "packing_volume_estimated"}
TARGET = "20261025_0015"
OUTPUT = ROOT / "outputs/packing-research"


def preview(db):
    products = (
        db.query(Product)
        .options(
            load_only(
                *(getattr(Product, c.name) for c in Product.__table__.columns if c.name not in NEW)
            )
        )
        .order_by(Product.id)
        .all()
    )
    boxes = db.query(BoxCatalogue).all()
    rows = []
    for product in products:
        proposal = reconciliation(product, boxes)
        rows.append(
            {
                "id": product.id,
                "sku": product.t_code,
                "previous_el": product.control_status.value,
                "proposed_el": proposal["fields"]["control_status"],
                **proposal,
            }
        )
    codes = {p.t_code for p in products}
    counts = Counter(r["code"] for r in sources()["products"])
    result = {
        "sources": sources()["sources"],
        "summary": {
            "products": len(products),
            "grouping_matches": sum(bool(r["grouping_rows"]) for r in rows),
            "duplicate_codes": {code: count for code, count in counts.items() if count > 1},
            "el_changes": sum(
                r["proposed_el"] is not None and r["previous_el"] != r["proposed_el"] for r in rows
            ),
            "unresolved_el": sum("control_status" in r["unresolved"] for r in rows),
            "mapped_limits": sum(len(r["limits"]) for r in rows),
            "ambiguous_limits": sum(len(r["limit_issues"]) for r in rows),
        },
        "unmatched_official_rows": [r for r in sources()["products"] if r["code"] not in codes],
        "products": rows,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "product-table-import-preview.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-project-ref")
    args = parser.parse_args()
    project = (engine.url.username or "").split(".")[-1]
    if project == "postgres":
        project = (engine.url.host or "").removeprefix("db.").split(".")[0]
    with Session(engine) as db:
        db.execute(sa.text("SET TRANSACTION READ ONLY"))
        report = preview(db)
    print(
        json.dumps(
            {
                "project_ref": project,
                **report["summary"],
                "unmatched_official_rows": len(report["unmatched_official_rows"]),
            }
        )
    )
    if not args.apply:
        return
    if project != args.expected_project_ref:
        raise RuntimeError("Configured database differs from expected project")
    if settings.PACKING_PROFILES_ENABLED:
        raise RuntimeError("Keep strict planning off during data preparation")
    backup = ROOT / ".codex-artifact/database-backups" / f"product-table-{uuid.uuid4().hex[:12]}"
    backup.mkdir(parents=True)
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        counts = snapshot(connection, backup / "application-data.json")
        history, columns = locked_history(connection)
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL lock_timeout='10s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout='180s'"))
        config.attributes["connection"] = connection
        command.upgrade(config, TARGET)
        with Session(bind=connection, expire_on_commit=False) as db:
            products = (
                db.query(Product)
                .options(selectinload(Product.packing_profiles), selectinload(Product.box_limits))
                .order_by(Product.id)
                .with_for_update()
                .all()
            )
            identities = {p.id for p in products}
            boxes = db.query(BoxCatalogue).order_by(BoxCatalogue.id).with_for_update().all()
            for product in products:
                apply_reconciliation(db, product, boxes)
            db.flush()
            summary = {
                "products": len(products),
                "volume_drafts": sum(p.packing_volume_l_per_kg is not None for p in products),
                "accepted_volumes": sum(p.packing_volume_reviewed for p in products),
                "single_product_limits": sum(len(p.box_limits) for p in products),
                "strict_planning": settings.PACKING_PROFILES_ENABLED,
            }
            db.add(
                AuditLog(
                    event_type="PRODUCT_TABLE_SOURCE_IMPORT",
                    actor_id="product-table-rollout",
                    actor_role="SYSTEM",
                    target_table="products",
                    target_id="source-import",
                    before_state=report["summary"],
                    after_state={**summary, "sources": sources()["sources"]},
                )
            )
            db.flush()
            assert identities == {p.id for p in db.query(Product)}
        after, _ = locked_history(connection, columns)
        if after != history:
            raise RuntimeError("Locked history changed; rolling back")
        for table in ["products", "client_order_items", "product_pack_profiles"]:
            actual = connection.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one()
            if actual != counts[table]:
                raise RuntimeError(f"{table} count changed; rolling back")
    result = {
        "migration": TARGET,
        **summary,
        "backup": str(backup / "application-data.json"),
        "locked_history_unchanged": True,
        "locked_history_sha256": history,
        "source_preview": "product-table-import-preview.json",
    }
    (OUTPUT / "product-table-rollout.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
