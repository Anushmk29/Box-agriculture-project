import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_receipt_migration_is_repeatable_and_refuses_to_discard_used_keys():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261027_0020_request_results.py"
    spec = importlib.util.spec_from_file_location("request_receipts", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()
        connection.execute(sa.text("INSERT INTO request_results (actor_id, operation, request_key, request_sha256, response_json) "
            "VALUES ('actor', 'op', 'kept-key', 'hash', '{}')"))
        migration.upgrade()
        assert connection.scalar(sa.text("SELECT count(*) FROM request_results")) == 1
        with pytest.raises(RuntimeError, match="durable request receipts"):
            migration.downgrade()
        assert sa.inspect(connection).has_table("request_results")
        connection.execute(sa.text("DELETE FROM request_results"))
        migration.downgrade()
        assert not sa.inspect(connection).has_table("request_results")
        migration.upgrade()
    engine.dispose()
