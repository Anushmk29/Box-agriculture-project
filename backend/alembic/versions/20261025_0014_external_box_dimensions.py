"""Use external workbook dimensions; remove separate internal measurements."""

import sqlalchemy as sa

from alembic import op

revision = "20261025_0014"
down_revision = "20261024_0013"
branch_labels = None
depends_on = None

INTERNAL_COLUMNS = ("internal_length_cm", "internal_width_cm", "internal_height_cm")


def upgrade():
    columns = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("box_catalogue")}
    removed = [name for name in INTERNAL_COLUMNS if name in columns]
    resize_volume = (
        "volume_m3" in columns and getattr(columns["volume_m3"]["type"], "scale", None) != 8
    )
    if removed or resize_volume:
        with op.batch_alter_table("box_catalogue") as batch:
            for name in removed:
                batch.drop_column(name)
            if resize_volume:
                batch.alter_column(
                    "volume_m3", existing_type=columns["volume_m3"]["type"], type_=sa.Numeric(12, 8)
                )
    if {"length_cm", "width_cm", "height_cm", "volume_m3"} <= columns.keys():
        op.execute(
            sa.text(
                "UPDATE box_catalogue SET volume_m3 = length_cm * width_cm * height_cm / 1000000.0"
            )
        )


def downgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("box_catalogue")}
    for name in INTERNAL_COLUMNS:
        if name not in columns:
            op.add_column("box_catalogue", sa.Column(name, sa.Numeric(8, 2), nullable=True))
    # External sizes cannot reconstruct removed internal measurements.
    op.execute(sa.text("UPDATE box_catalogue SET packing_reviewed = false"))
