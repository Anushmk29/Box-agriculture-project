import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_source_integrity_migration_preserves_legacy_paths_and_rejects_lossy_downgrade():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261027_0021_source_integrity.py"
    spec = importlib.util.spec_from_file_location("source_integrity", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        connection.exec_driver_sql("CREATE TABLE raw_order_files (id VARCHAR(36) PRIMARY KEY, storage_url VARCHAR(512) NOT NULL)")
        connection.exec_driver_sql("INSERT INTO raw_order_files VALUES ('kept', '/original/source.xlsx')")
        migration.upgrade()
        migration.upgrade()
        row = connection.exec_driver_sql("SELECT storage_url, file_sha256, file_size_bytes FROM raw_order_files").one()
        assert tuple(row) == ("/original/source.xlsx", None, None)
        for values in [(None, 2), ("f"*64, None), ("short", 2), ("f"*64, 0), ("f"*64, 26214401)]:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(sa.text("UPDATE raw_order_files SET file_sha256=:sha, file_size_bytes=:size"),
                                   {"sha": values[0], "size": values[1]})
        connection.execute(sa.text("UPDATE raw_order_files SET file_sha256=:sha, file_size_bytes=4"), {"sha": "f"*64})
        with pytest.raises(RuntimeError, match="integrity metadata"):
            migration.downgrade()
        connection.exec_driver_sql("UPDATE raw_order_files SET file_sha256=NULL, file_size_bytes=NULL")
        migration.downgrade()
        assert connection.exec_driver_sql("SELECT storage_url FROM raw_order_files").scalar() == "/original/source.xlsx"
        migration.upgrade()
    engine.dispose()
