"""Unreviewed profile drafts, evidence and original order quantity provenance."""

import sqlalchemy as sa

from alembic import op

revision = "20261024_0013"
down_revision = "20261023_0012"
branch_labels = None
depends_on = None

RULE = "(net_weight_kg IS NULL OR net_weight_kg > 0) AND (volume_cm3 IS NULL OR volume_cm3 > 0) AND (NOT reviewed OR (net_weight_kg IS NOT NULL AND volume_cm3 IS NOT NULL))"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"]: c for c in inspector.get_columns("product_pack_profiles")}
    checks = {
        c["name"]: c["sqltext"] for c in inspector.get_check_constraints("product_pack_profiles")
    }
    if not columns["volume_cm3"]["nullable"] or "IS NULL" not in checks.get(
        "ck_pack_profile_positive", ""
    ):
        with op.batch_alter_table("product_pack_profiles") as batch:
            if "ck_pack_profile_positive" in checks:
                batch.drop_constraint("ck_pack_profile_positive", type_="check")
            batch.alter_column("net_weight_kg", existing_type=sa.Numeric(10, 4), nullable=True)
            batch.alter_column("volume_cm3", existing_type=sa.Numeric(12, 2), nullable=True)
            batch.create_check_constraint("ck_pack_profile_positive", RULE)
    if "evidence" not in columns:
        op.add_column("product_pack_profiles", sa.Column("evidence", sa.JSON(), nullable=True))
    tables = set(inspector.get_table_names())
    if "client_order_items" in tables:
        columns = {
            c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("client_order_items")
        }
        if "quantity_source" not in columns:
            op.add_column(
                "client_order_items", sa.Column("quantity_source", sa.JSON(), nullable=True)
            )
    for table, names in [
        ("client_order_items", ["estimated_net_weight_kg", "confirmed_net_weight_kg"]),
        ("client_orders", ["total_estimated_net_weight_kg"]),
    ]:
        if table not in tables:
            continue
        columns = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(table)}
        for name in names:
            if name in columns and getattr(columns[name]["type"], "scale", None) != 4:
                with op.batch_alter_table(table) as batch:
                    batch.alter_column(
                        name, existing_type=columns[name]["type"], type_=sa.Numeric(12, 4)
                    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT count(*) FROM product_pack_profiles WHERE net_weight_kg IS NULL OR volume_cm3 IS NULL"
        )
    ).scalar():
        raise RuntimeError(
            "Export or complete draft packing profiles before downgrading; no drafts were deleted"
        )
    with op.batch_alter_table("product_pack_profiles") as batch:
        batch.drop_constraint("ck_pack_profile_positive", type_="check")
        batch.alter_column("net_weight_kg", existing_type=sa.Numeric(10, 4), nullable=False)
        batch.alter_column("volume_cm3", existing_type=sa.Numeric(12, 2), nullable=False)
        batch.create_check_constraint(
            "ck_pack_profile_positive", "net_weight_kg > 0 AND volume_cm3 > 0"
        )
        batch.drop_column("evidence")
    op.drop_column("client_order_items", "quantity_source")
    # Retain added weight precision to avoid rounding historical order quantities.
