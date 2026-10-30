"""Reviewed packing profiles, internal box capacity and staff-selected EL mode."""

import sqlalchemy as sa

from alembic import op

revision = "20261023_0012"
down_revision = "20261012_0011"
branch_labels = None
depends_on = None


def _add_if_missing(table, column):
    if column.name not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, column)


def upgrade():
    for column in [
        sa.Column("firmness", sa.String(16), nullable=True),
        sa.Column("packing_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("packing_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("packing_notes", sa.String(2048), nullable=True),
        sa.Column("packing_source", sa.JSON(), nullable=True),
    ]:
        _add_if_missing("products", column)
    for column in [
        sa.Column("internal_length_cm", sa.Numeric(8, 2), nullable=True),
        sa.Column("internal_width_cm", sa.Numeric(8, 2), nullable=True),
        sa.Column("internal_height_cm", sa.Numeric(8, 2), nullable=True),
        sa.Column("packing_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("packing_revision", sa.Integer(), nullable=False, server_default="1"),
    ]:
        _add_if_missing("box_catalogue", column)
    for column in [
        sa.Column("packing_mode", sa.String(16), nullable=True),
        sa.Column("packing_snapshot", sa.JSON(), nullable=True),
        sa.Column("shipping_dimensions_snapshot", sa.JSON(), nullable=True),
    ]:
        _add_if_missing("boxes", column)
    if "product_pack_profiles" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "product_pack_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("pack_size", sa.String(64), nullable=False),
            sa.Column("pack_key", sa.String(128), nullable=False),
            sa.Column("aliases", sa.JSON(), nullable=False),
            sa.Column("net_weight_kg", sa.Numeric(10, 4), nullable=False),
            sa.Column("volume_cm3", sa.Numeric(12, 2), nullable=False),
            sa.Column("reviewed", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.UniqueConstraint("product_id", "pack_key", name="uq_product_pack_profile"),
            sa.CheckConstraint(
                "net_weight_kg > 0 AND volume_cm3 > 0", name="ck_pack_profile_positive"
            ),
        )
        op.create_index(
            "ix_product_pack_profiles_product_id", "product_pack_profiles", ["product_id"]
        )
    if "product_box_limits" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "product_box_limits",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
            sa.Column(
                "box_catalogue_id", sa.String(36), sa.ForeignKey("box_catalogue.id"), nullable=False
            ),
            sa.Column("max_net_weight_kg", sa.Numeric(8, 2), nullable=False),
            sa.Column("reviewed", sa.Boolean(), nullable=False),
            sa.Column("source_note", sa.String(2048), nullable=True),
            sa.UniqueConstraint("product_id", "box_catalogue_id", name="uq_product_box_limit"),
            sa.CheckConstraint("max_net_weight_kg > 0", name="ck_product_box_limit_positive"),
        )
        op.create_index("ix_product_box_limits_product_id", "product_box_limits", ["product_id"])

    if op.get_bind().dialect.name == "postgresql":
        # App authentication is enforced by FastAPI, not public Data API policies.
        # Keep newly introduced tables unavailable to direct client-role access.
        op.execute("ALTER TABLE product_pack_profiles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE product_box_limits ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("product_box_limits")
    op.drop_table("product_pack_profiles")
    for name in ["shipping_dimensions_snapshot", "packing_snapshot", "packing_mode"]:
        op.drop_column("boxes", name)
    for name in [
        "packing_revision",
        "packing_reviewed",
        "internal_height_cm",
        "internal_width_cm",
        "internal_length_cm",
    ]:
        op.drop_column("box_catalogue", name)
    for name in [
        "packing_source",
        "packing_notes",
        "packing_revision",
        "packing_reviewed",
        "firmness",
    ]:
        op.drop_column("products", name)
