import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh_schema", [False, True])
def test_packing_profile_upgrade_supports_existing_and_fresh_schemas(fresh_schema):
    engine = sa.create_engine("sqlite://")
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261023_0012_packing_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("packing_profiles_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        if fresh_schema:
            # Initial migration intentionally uses current model metadata.
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql("CREATE TABLE products (id VARCHAR(36) PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE box_catalogue (id VARCHAR(36) PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE boxes (id VARCHAR(36) PRIMARY KEY, actual_net_weight_kg NUMERIC, actual_gross_weight_kg NUMERIC)"
            )
            connection.exec_driver_sql("INSERT INTO products (id) VALUES ('existing-sku')")
            connection.exec_driver_sql("INSERT INTO boxes VALUES ('historical-box', 4, 4.5)")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        inspector = sa.inspect(connection)
        assert {"product_pack_profiles", "product_box_limits"} <= set(inspector.get_table_names())
        assert "packing_mode" in {column["name"] for column in inspector.get_columns("boxes")}
        if not fresh_schema:
            row = connection.execute(
                sa.text(
                    "SELECT actual_net_weight_kg, actual_gross_weight_kg, packing_mode FROM boxes"
                )
            ).one()
            assert tuple(row) == (4, 4.5, None)
            assert (
                connection.execute(sa.text("SELECT packing_reviewed FROM products")).scalar() == 0
            )
        with Operations.context(context):
            migration.upgrade()  # Also safe after create_all or a repeated deployment check.
        if not fresh_schema:
            with Operations.context(context):
                migration.downgrade()
            assert connection.execute(sa.text("SELECT count(*) FROM products")).scalar() == 1
    engine.dispose()
