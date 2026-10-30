"""Index scoped price labels and adjacent effective-date lookup without rewriting history."""
import sqlalchemy as sa

from alembic import op

revision = "20261027_0022"
down_revision = "20261027_0021"
branch_labels = None
depends_on = None

INDEXES = (
    ("price_lists", "ix_price_scope_pack_day", ["product_id", "customer_id", "pack_size"]),
    ("customer_selling_prices", "ix_selling_scope_pack_day", ["main_customer_id", "sub_customer_id", "product_id", "pack_size"]),
)


def _exists(table, name):
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # SQLAlchemy's SQLite reflection omits expression indexes.
        return bool(connection.execute(sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name AND tbl_name=:table"),
                                       {"name": name, "table": table}).first())
    return name in {index["name"] for index in sa.inspect(connection).get_indexes(table)}


def upgrade():
    # Ordinary transactional DDL: rehearse lock/build time in isolated Supabase
    # before scheduling this migration; no autocommit or second authority.
    for table, name, scope in INDEXES:
        if not _exists(table, name):
            op.create_index(name, table, [*scope, sa.text("replace(trim(valid_from), '-', '')"), "id"])


def downgrade():
    for table, name, _ in reversed(INDEXES):
        if _exists(table, name):
            op.drop_index(name, table_name=table)
