import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
def test_product_volume_migration_is_repeatable_and_preserves_existing_data(fresh):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261025_0015_product_volume.py"
    spec = importlib.util.spec_from_file_location("product_volume_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql(
                "CREATE TABLE products (id VARCHAR PRIMARY KEY, name_en VARCHAR)"
            )
            connection.exec_driver_sql("INSERT INTO products VALUES ('keep','Existing product')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        if not fresh:
            assert tuple(
                connection.execute(
                    sa.text(
                        "SELECT id,name_en,packing_volume_l_per_kg,packing_volume_reviewed,packing_volume_estimated FROM products"
                    )
                ).one()
            ) == ("keep", "Existing product", None, 0, 1)
