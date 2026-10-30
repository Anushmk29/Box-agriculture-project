"""Retain gram fractions in packed and billed item weights."""

import sqlalchemy as sa

from alembic import op

revision = "20261026_0017"
down_revision = "20261026_0016"
branch_labels = None
depends_on = None

WEIGHT_COLUMNS = [("box_items", "final_net_weight_kg"), ("invoice_line_items", "billed_weight_kg")]


def upgrade():
    for table, name in WEIGHT_COLUMNS:
        column = next(c for c in sa.inspect(op.get_bind()).get_columns(table) if c["name"] == name)
        if column["type"].precision == 10 and column["type"].scale == 4:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(name, existing_type=column["type"], type_=sa.Numeric(10, 4),
                               existing_nullable=False)


def downgrade():
    connection = op.get_bind()
    for table, name in WEIGHT_COLUMNS:
        # Table/column names above are fixed migration identifiers, never user input.
        if connection.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE {name} != round({name}, 2)"
        )).scalar():
            raise RuntimeError("Cannot discard recorded packed/billed weight precision")
    for table, name in WEIGHT_COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(name, existing_type=sa.Numeric(10, 4), type_=sa.Numeric(8, 2),
                               existing_nullable=False)
