"""Indexes for operational register filters, pagination and summary counts."""
import sqlalchemy as sa

from alembic import op

revision = "20261012_0011"
down_revision = "20261009_0010"
branch_labels = None
depends_on = None

INDEXES = [
    ("raw_order_files", "ix_raw_files_status_created_id", ["status", "created_at", "id"]),
    ("raw_order_files", "ix_raw_files_shipment_route", ["shipment_target_date", "route_line"]),
    ("client_orders", "ix_orders_status_created_id", ["status", "created_at", "id"]),
    ("client_orders", "ix_orders_raw_file", ["raw_order_file_id"]),
    ("master_purchasing_lists", "ix_purchase_status_created_id", ["status", "created_at", "id"]),
    ("master_purchasing_lists", "ix_purchase_shipment_route", ["shipment_date", "route_code"]),
    ("purchasing_items", "ix_purchase_items_list", ["purchasing_list_id"]),
    ("purchasing_order_sources", "ix_purchase_sources_file_list", ["raw_order_file_id", "purchasing_list_id"]),
    ("boxes", "ix_boxes_order_weights", ["client_order_id", "actual_net_weight_kg", "actual_gross_weight_kg"]),
    ("invoices", "ix_invoice_status_created_id", ["status", "created_at", "id"]),
    ("invoice_line_items", "ix_invoice_lines_source_invoice", ["source_order_id", "invoice_id"]),
    ("invoice_line_items", "ix_invoice_lines_invoice", ["invoice_id"]),
    ("audit_logs", "ix_audit_target_created_id", ["target_table", "target_id", "created_at", "id"]),
]


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, name, columns in INDEXES:
        if name not in {index["name"] for index in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade():
    for table, name, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
