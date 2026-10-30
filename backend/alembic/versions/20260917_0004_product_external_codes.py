"""Add searchable external product identifiers for existing deployments."""

import sqlalchemy as sa

from alembic import op

revision = "20260917_0004"
down_revision = "20260910_0003"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    product_columns = _columns("products")
    if "barcode" not in product_columns:
        op.add_column("products", sa.Column("barcode", sa.String(32), nullable=True))
    if "norway_code" not in product_columns:
        op.add_column("products", sa.Column("norway_code", sa.String(32), nullable=True))

    product_indexes = _indexes("products")
    if "ix_products_barcode" not in product_indexes:
        op.create_index("ix_products_barcode", "products", ["barcode"])
    if "ix_products_norway_code" not in product_indexes:
        op.create_index("ix_products_norway_code", "products", ["norway_code"])


def downgrade() -> None:
    product_indexes = _indexes("products")
    if "ix_products_norway_code" in product_indexes:
        op.drop_index("ix_products_norway_code", table_name="products")
    if "ix_products_barcode" in product_indexes:
        op.drop_index("ix_products_barcode", table_name="products")
    product_columns = _columns("products")
    if "norway_code" in product_columns:
        op.drop_column("products", "norway_code")
    if "barcode" in product_columns:
        op.drop_column("products", "barcode")
