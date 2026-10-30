import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
def test_revision_migration_preserves_rows_and_never_resets_used_tokens(fresh):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261027_0019_aggregate_revisions.py"
    spec = importlib.util.spec_from_file_location("aggregate_revisions", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            for table in ("invoices", "client_orders"):
                connection.exec_driver_sql(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY, marker TEXT)")
                connection.exec_driver_sql(f"INSERT INTO {table} VALUES ('kept', 'unchanged')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            for table, name in migration.REVISION_COLUMNS:
                metadata = {column["name"]: column for column in sa.inspect(connection).get_columns(table)}
                assert not metadata[name]["nullable"]
                if not fresh:
                    assert tuple(connection.exec_driver_sql(f"SELECT marker, {name} FROM {table}").one()) == ("unchanged", 1)
            if not fresh:
                connection.exec_driver_sql("UPDATE client_orders SET packing_revision=9")
                migration.upgrade()
                assert connection.exec_driver_sql("SELECT packing_revision FROM client_orders").scalar() == 9
                with pytest.raises(RuntimeError, match="used revision"):
                    migration.downgrade()
                # Preflight all tables before dropping any column.
                assert "revision" in {c["name"] for c in sa.inspect(connection).get_columns("invoices")}
                connection.exec_driver_sql("UPDATE client_orders SET packing_revision=1")
            migration.downgrade()
            for table, name in migration.REVISION_COLUMNS:
                assert name not in {c["name"] for c in sa.inspect(connection).get_columns(table)}
            migration.upgrade()
    engine.dispose()
