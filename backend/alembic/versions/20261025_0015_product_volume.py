"""Product occupied volume per kilogram; retain historical pack measurements."""

import sqlalchemy as sa

from alembic import op

revision = "20261025_0015"
down_revision = "20261025_0014"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("products")}
    for column in [
        sa.Column("packing_volume_l_per_kg", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "packing_volume_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "packing_volume_estimated", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    ]:
        if column.name not in columns:
            op.add_column("products", column)


def downgrade():
    with op.batch_alter_table("products") as batch:
        for name in [
            "packing_volume_estimated",
            "packing_volume_reviewed",
            "packing_volume_l_per_kg",
        ]:
            batch.drop_column(name)
