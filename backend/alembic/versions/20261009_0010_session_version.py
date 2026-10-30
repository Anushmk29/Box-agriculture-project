"""Invalidate sessions after password changes without rewriting historical invoices."""
import sqlalchemy as sa

from alembic import op

revision = "20261009_0010"
down_revision = "20261006_0009"
branch_labels = None
depends_on = None


def upgrade():
    # The original bootstrap migration creates tables from the current models.
    if "session_version" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}:
        op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("users", "session_version")
