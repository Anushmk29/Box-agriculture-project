import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
def test_evidence_migration_preserves_history_and_enforces_reviewed_completeness(fresh):
    path = (
        Path(__file__).resolve().parents[1] / "alembic/versions/20261024_0013_packing_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("packing_evidence_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql("CREATE TABLE products (id VARCHAR PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE product_pack_profiles (id VARCHAR PRIMARY KEY,product_id VARCHAR,pack_size VARCHAR,pack_key VARCHAR,net_weight_kg NUMERIC NOT NULL,volume_cm3 NUMERIC NOT NULL,reviewed BOOLEAN,CONSTRAINT ck_pack_profile_positive CHECK(net_weight_kg>0 AND volume_cm3>0))"
            )
            connection.exec_driver_sql(
                "INSERT INTO product_pack_profiles VALUES ('old','sku','100g','100g',.1,500,1)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE client_order_items (id VARCHAR PRIMARY KEY, estimated_net_weight_kg NUMERIC(10,2),confirmed_net_weight_kg NUMERIC(10,2))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE client_orders (id VARCHAR PRIMARY KEY,total_estimated_net_weight_kg NUMERIC(10,2))"
            )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        if not fresh:
            assert tuple(
                connection.execute(
                    sa.text(
                        "SELECT net_weight_kg,volume_cm3,reviewed FROM product_pack_profiles WHERE id='old'"
                    )
                ).one()
            ) == (0.1, 500, 1)
            connection.exec_driver_sql(
                "INSERT INTO product_pack_profiles(id,product_id,pack_size,pack_key,reviewed) VALUES('draft','sku','1pc','1pc',0)"
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE product_pack_profiles SET reviewed=1 WHERE id='draft'"
                )
        assert "evidence" in {
            c["name"] for c in sa.inspect(connection).get_columns("product_pack_profiles")
        }
        assert "quantity_source" in {
            c["name"] for c in sa.inspect(connection).get_columns("client_order_items")
        }
