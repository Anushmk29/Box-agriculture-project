import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
def test_template_and_cutoff_migration_preserves_rows_and_repeats_safely(fresh):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261027_0024_excel_templates_material_policy.py"
    )
    spec = importlib.util.spec_from_file_location("excel_template_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql(
                "CREATE TABLE products (id VARCHAR PRIMARY KEY, t_code VARCHAR, packing_reviewed BOOLEAN, packing_revision INTEGER)"
            )
            connection.exec_driver_sql(
                "INSERT INTO products VALUES ('garlic', '11-035', true, 4), ('spring', '11-046', true, 4), ('unreviewed', '16-035', false, 4)"
            )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        assert "excel_templates" in sa.inspect(connection).get_table_names()
        if not fresh:
            assert connection.exec_driver_sql(
                "SELECT foam_below_kg, packing_revision FROM products WHERE id='garlic'"
            ).one() == (1, 5)
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM products WHERE foam_below_kg IS NULL"
                ).scalar()
                == 2
            )
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert "foam_below_kg" not in {
            c["name"] for c in sa.inspect(connection).get_columns("products")
        }
    engine.dispose()
