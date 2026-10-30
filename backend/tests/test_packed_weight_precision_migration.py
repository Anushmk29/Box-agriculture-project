import importlib.util
from collections import defaultdict
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("filename,old_precision,new_precision", [
    ("20261026_0017_packed_weight_precision.py", 8, 10),
    ("20261026_0018_procurement_weight_precision.py", 10, 12),
])
def test_weight_precision_upgrade_preserves_history_and_guards_lossy_downgrade(
    fresh, filename, old_precision, new_precision,
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location("packed_weight_precision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            tables = defaultdict(list)
            for table, column in migration.WEIGHT_COLUMNS:
                tables[table].append(column)
            for table, names in tables.items():
                columns = ",".join(f"{column} NUMERIC({old_precision}, 2) NOT NULL" for column in names)
                connection.exec_driver_sql(
                    f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY, {columns})"
                )
                values = ",".join("1.25" for _ in names)
                connection.exec_driver_sql(f"INSERT INTO {table} VALUES ('historical', {values})")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            for table, name in migration.WEIGHT_COLUMNS:
                column = next(c for c in sa.inspect(connection).get_columns(table) if c["name"] == name)
                assert (column["type"].precision, column["type"].scale) == (new_precision, 4)
                if not fresh:
                    assert connection.execute(sa.text(f"SELECT {name} FROM {table}")).scalar() == 1.25
            if not fresh:
                table, name = migration.WEIGHT_COLUMNS[0]
                connection.exec_driver_sql(f"UPDATE {table} SET {name}=0.125")
                with pytest.raises(RuntimeError, match="precision"):
                    migration.downgrade()
                connection.exec_driver_sql(f"UPDATE {table} SET {name}=1.25")
            migration.downgrade()
            migration.upgrade()
    engine.dispose()
