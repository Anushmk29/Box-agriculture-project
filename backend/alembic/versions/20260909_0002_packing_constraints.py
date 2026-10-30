"""Persist confirmed quantities and packing constraint master data."""

import sqlalchemy as sa

from alembic import op

revision = "20260909_0002"
down_revision = "20260909_0001"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    order_item_columns = _columns("client_order_items")
    if "confirmed_pack_count" not in order_item_columns:
        op.add_column("client_order_items", sa.Column("confirmed_pack_count", sa.Integer()))
    if "confirmed_net_weight_kg" not in order_item_columns:
        op.add_column(
            "client_order_items",
            sa.Column("confirmed_net_weight_kg", sa.Numeric(10, 2)),
        )

    product_column_details = {
        column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("products")
    }
    product_columns = set(product_column_details)
    if "botanical_family" not in product_columns:
        op.add_column("products", sa.Column("botanical_family", sa.String(128)))
    if "default_unit_volume_cm3" not in product_columns:
        op.add_column("products", sa.Column("default_unit_volume_cm3", sa.Numeric(10, 2)))
    op.execute(
        sa.text(
            "UPDATE products SET default_unit_volume_cm3 = 1000 "
            "WHERE default_unit_volume_cm3 IS NULL"
        )
    )
    volume_was_nullable = product_column_details.get("default_unit_volume_cm3", {}).get(
        "nullable", True
    )
    if volume_was_nullable:
        with op.batch_alter_table("products") as batch_op:
            batch_op.alter_column(
                "default_unit_volume_cm3",
                existing_type=sa.Numeric(10, 2),
                nullable=False,
            )

    inspector = sa.inspect(op.get_bind())
    index_names = {index["name"] for index in inspector.get_indexes("products")}
    if "ix_products_botanical_family" not in index_names:
        op.create_index("ix_products_botanical_family", "products", ["botanical_family"])


def downgrade() -> None:
    op.drop_index("ix_products_botanical_family", table_name="products")
    op.drop_column("products", "default_unit_volume_cm3")
    op.drop_column("products", "botanical_family")
    op.drop_column("client_order_items", "confirmed_net_weight_kg")
    op.drop_column("client_order_items", "confirmed_pack_count")
