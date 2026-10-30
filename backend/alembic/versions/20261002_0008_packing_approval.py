"""Manager packing approval queue."""

import sqlalchemy as sa

from alembic import op

revision = "20261002_0008"
down_revision = "20260930_0007"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("client_orders")}
    columns = [
        sa.Column("packing_approval_status", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("packing_requested_by", sa.String(36), nullable=True),
        sa.Column("packing_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("packing_review_note", sa.String(512), nullable=True),
    ]
    for column in columns:
        if column.name not in existing:
            op.add_column("client_orders", column)
    if not any(fk["constrained_columns"] == ["packing_requested_by"] for fk in inspector.get_foreign_keys("client_orders")):
        op.create_foreign_key("fk_packing_requested_by", "client_orders", "users", ["packing_requested_by"], ["id"])


def downgrade():
    op.drop_constraint("fk_packing_requested_by", "client_orders", type_="foreignkey")
    for name in ("packing_review_note", "packing_requested_at", "packing_requested_by", "packing_approval_status"):
        op.drop_column("client_orders", name)
