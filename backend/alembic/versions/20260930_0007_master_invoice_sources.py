"""Link consolidated invoice lines to their source orders."""

import sqlalchemy as sa

from alembic import op

revision = "20260930_0007"
down_revision = "20260929_0006"
branch_labels = None
depends_on = None

INDEX = "ix_invoice_line_items_source_order_id"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    columns = {c["name"] for c in _inspector().get_columns("invoice_line_items")}
    if "source_order_id" not in columns:
        op.add_column(
            "invoice_line_items",
            sa.Column("source_order_id", sa.String(36), nullable=True),
        )
        op.create_foreign_key(
            "fk_invoice_line_items_source_order_id",
            "invoice_line_items",
            "client_orders",
            ["source_order_id"],
            ["id"],
        )
    op.execute(
        "UPDATE invoice_line_items SET source_order_id = "
        "(SELECT client_order_id FROM invoices WHERE invoices.id = invoice_line_items.invoice_id) "
        "WHERE source_order_id IS NULL"
    )
    if INDEX not in {i["name"] for i in _inspector().get_indexes("invoice_line_items")}:
        op.create_index(INDEX, "invoice_line_items", ["source_order_id"])


def downgrade() -> None:
    if INDEX in {i["name"] for i in _inspector().get_indexes("invoice_line_items")}:
        op.drop_index(INDEX, table_name="invoice_line_items")
    columns = {c["name"] for c in _inspector().get_columns("invoice_line_items")}
    if "source_order_id" in columns:
        op.drop_constraint(
            "fk_invoice_line_items_source_order_id",
            "invoice_line_items",
            type_="foreignkey",
        )
        op.drop_column("invoice_line_items", "source_order_id")
