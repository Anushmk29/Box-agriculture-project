import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


@pytest.mark.parametrize("fresh", [False, True])
def test_substitution_upgrade_preserves_order_and_enforces_reference(fresh):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261026_0016_order_substitutions.py"
    spec = importlib.util.spec_from_file_location("order_substitutions", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        if fresh:
            Base.metadata.create_all(connection)
        else:
            connection.exec_driver_sql("CREATE TABLE products (id VARCHAR(36) PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE client_order_items (id VARCHAR(36) PRIMARY KEY, product_id VARCHAR(36))"
            )
            connection.exec_driver_sql("INSERT INTO products VALUES ('original'), ('substitute')")
            connection.exec_driver_sql("INSERT INTO client_order_items VALUES ('line', 'original')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            inspector = sa.inspect(connection)
            assert "replacement_product_id" in {
                c["name"] for c in inspector.get_columns("client_order_items")
            }
            assert any(fk["constrained_columns"] == ["replacement_product_id"]
                       and fk["referred_table"] == "products"
                       for fk in inspector.get_foreign_keys("client_order_items"))
            if not fresh:
                assert tuple(connection.execute(sa.text(
                    "SELECT product_id,replacement_product_id FROM client_order_items"
                )).one()) == ("original", None)
                connection.exec_driver_sql(
                    "UPDATE client_order_items SET replacement_product_id='substitute'"
                )
                with pytest.raises(RuntimeError, match="lineage"):
                    migration.downgrade()
                connection.exec_driver_sql(
                    "UPDATE client_order_items SET replacement_product_id=NULL"
                )
            migration.downgrade()
            assert "replacement_product_id" not in {
                c["name"] for c in sa.inspect(connection).get_columns("client_order_items")
            }
            migration.upgrade()
    engine.dispose()
