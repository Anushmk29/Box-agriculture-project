"""Back up and migrate to workbook external dimensions without changing locked packing."""

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from rollout_packing_profiles import locked_history, snapshot

from app.core.database import engine
from app.models.audit import AuditLog
from app.models.master_data import BoxCatalogue
from app.modules.packing.catalogue import dimension_reference, reference_for

TARGET = "20261025_0014"


def verify_catalogue(db):
    boxes = db.query(BoxCatalogue).all()
    active = [b for b in boxes if b.is_active]
    rows = [reference_for(b) for b in active]
    if (
        any(row is None for row in rows)
        or len({row["row"] for row in rows}) != 15
        or len(active) != 15
    ):
        raise RuntimeError("Active catalogue must contain exactly the 15 workbook sizes")
    return active


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-project-ref")
    args = parser.parse_args()
    project = (engine.url.username or "").split(".")[-1]
    if project == "postgres":
        project = (engine.url.host or "").removeprefix("db.").split(".")[0]
    reference = dimension_reference()
    workbook = ROOT / "documents/box size" / reference["source_file"]
    if hashlib.sha256(workbook.read_bytes()).hexdigest() != reference["source_sha256"]:
        raise RuntimeError("Workbook changed; regenerate and verify its dimension reference first")
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION READ ONLY"))
        previous = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    print(json.dumps({"project_ref": project, "migration": previous, "apply": args.apply}))
    if not args.apply:
        return
    if project != args.expected_project_ref:
        raise RuntimeError("Configured database differs from --expected-project-ref")
    backup = ROOT / ".codex-artifact/database-backups" / f"external-boxes-{uuid.uuid4().hex[:12]}"
    backup.mkdir(parents=True)
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        snapshot(connection, backup / "application-data.json")
        history, old_columns = locked_history(connection)
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL lock_timeout='10s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout='120s'"))
        config.attributes["connection"] = connection
        command.upgrade(config, TARGET)
        with Session(bind=connection, expire_on_commit=False) as db:
            active = verify_catalogue(db)
            if previous != TARGET:
                for box in active:
                    box.packing_revision += 1
                db.add(
                    AuditLog(
                        event_type="BOX_DIMENSION_BASIS_UPDATED",
                        actor_id="external-box-rollout",
                        actor_role="SYSTEM",
                        target_table="box_catalogue",
                        target_id="workbook",
                        before_state={"migration": previous},
                        after_state={
                            "dimension_basis": "EXTERNAL",
                            "source": reference["source_file"],
                            "sha256": reference["source_sha256"],
                            "box_types": len(active),
                        },
                    )
                )
                db.flush()
        after, _ = locked_history(connection, old_columns)
        if after != history:
            raise RuntimeError("Locked history changed; rolling back")
        columns = {c["name"] for c in sa.inspect(connection).get_columns("box_catalogue")}
        if any(name.startswith("internal_") for name in columns):
            raise RuntimeError("Separate internal measurements were not removed")
        if (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            != TARGET
        ):
            raise RuntimeError("Unexpected migration version")
    report = {
        "migration": TARGET,
        "box_types": 15,
        "dimension_basis": "EXTERNAL",
        "source_sha256": reference["source_sha256"],
        "locked_history_unchanged": True,
        "locked_history_sha256": history,
        "internal_measurement_columns": 0,
    }
    output = ROOT / "outputs/packing-research/external-box-dimensions-rollout.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
