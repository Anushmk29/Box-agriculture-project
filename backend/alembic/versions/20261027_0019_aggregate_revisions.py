"""Detect stale invoice and packing writes without replacing business history."""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0019"
down_revision = "20261026_0018"
branch_labels = None
depends_on = None

REVISION_COLUMNS = (("invoices", "revision"), ("client_orders", "packing_revision"))


def upgrade():
    connection = op.get_bind()
    for table, name in REVISION_COLUMNS:
        if name not in {column["name"] for column in sa.inspect(connection).get_columns(table)}:
            op.add_column(table, sa.Column(name, sa.BigInteger(), nullable=False, server_default="1"))


def downgrade():
    connection = op.get_bind()
    # Never reset live conflict tokens. Roll back application code only through
    # a coordinated release; dropping used revisions would allow stale tokens.
    for table, name in REVISION_COLUMNS:
        columns = {column["name"] for column in sa.inspect(connection).get_columns(table)}
        if name in columns and connection.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE {name} != 1"
        )).scalar():
            raise RuntimeError("Cannot discard used revision tokens; coordinate the rollback")
    for table, name in reversed(REVISION_COLUMNS):
        if name in {column["name"] for column in sa.inspect(connection).get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
