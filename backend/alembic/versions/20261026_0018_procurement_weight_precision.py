"""Preserve the same four-decimal kilogram precision at the purchasing boundary."""

import sqlalchemy as sa

from alembic import op

revision = "20261026_0018"
down_revision = "20261026_0017"
branch_labels = None
depends_on = None

WEIGHT_COLUMNS = [("purchasing_items", column) for column in (
    "ordered_net_weight_kg", "buffer_weight_kg", "total_required_weight_kg", "confirmed_purchased_weight_kg"
)]


def upgrade():
    columns = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("purchasing_items")}
    with op.batch_alter_table("purchasing_items") as batch:
        for _, name in WEIGHT_COLUMNS:
            column = columns[name]
            if column["type"].precision == 12 and column["type"].scale == 4:
                continue
            batch.alter_column(name, existing_type=column["type"], type_=sa.Numeric(12, 4),
                               existing_nullable=column["nullable"])


def downgrade():
    connection = op.get_bind()
    for _, name in WEIGHT_COLUMNS:
        if connection.execute(sa.text(
            f"SELECT count(*) FROM purchasing_items WHERE {name} != round({name}, 2)"
        )).scalar():
            raise RuntimeError("Cannot discard recorded procurement weight precision")
    columns = {column["name"]: column for column in sa.inspect(connection).get_columns("purchasing_items")}
    with op.batch_alter_table("purchasing_items") as batch:
        for _, name in WEIGHT_COLUMNS:
            batch.alter_column(name, existing_type=sa.Numeric(12, 4), type_=sa.Numeric(10, 2),
                               existing_nullable=columns[name]["nullable"])
