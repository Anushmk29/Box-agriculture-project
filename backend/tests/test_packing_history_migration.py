import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261031_0025_packing_evidence.py"
    spec = importlib.util.spec_from_file_location("packing_evidence_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_evidence_migration_is_repeatable_and_empty_downgrade_works():
    migration = migration_module()
    with sa.create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE products (id VARCHAR PRIMARY KEY, name_en VARCHAR)")
        connection.exec_driver_sql("INSERT INTO products VALUES ('keep', 'Existing product')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert {"packing_history_reviews", "packing_feedback"} <= set(sa.inspect(connection).get_table_names())
            assert connection.scalar(sa.text("SELECT name_en FROM products WHERE id='keep'")) == "Existing product"
            migration.downgrade()
        assert "packing_feedback" not in sa.inspect(connection).get_table_names()


def test_downgrade_refuses_to_discard_recorded_evidence():
    migration = migration_module()
    with sa.create_engine("sqlite://").begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            connection.execute(sa.text("""INSERT INTO packing_history_reviews
                (id, example_id, source_version, status, revision, definition, note, reviewed_by, updated_at)
                VALUES ('keep', 'source', 'version', 'REJECTED', 1, '{}', 'Retain evidence', 'staff', CURRENT_TIMESTAMP)"""))
            with pytest.raises(RuntimeError, match="preserve"):
                migration.downgrade()
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM packing_history_reviews")) == 1
