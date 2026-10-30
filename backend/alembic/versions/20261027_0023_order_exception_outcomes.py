"""Add terminal operational outcomes without changing historical financial rows."""
import sqlalchemy as sa

from alembic import op

revision = "20261027_0023"
down_revision = "20261027_0022"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        # No rows use these values inside this migration transaction.
        op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")
        op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'UNAVAILABLE'")
    # SQLite stores this non-native enum as VARCHAR without a CHECK constraint.


def downgrade():
    connection = op.get_bind()
    for table in ("client_orders", "raw_order_files", "master_purchasing_lists"):
        if connection.execute(sa.text(f"SELECT count(*) FROM {table} WHERE CAST(status AS VARCHAR) IN ('CANCELLED','UNAVAILABLE')")).scalar():
            raise RuntimeError("Cannot discard used order outcomes; roll back code with a compatible enum")
    # Supabase/PostgreSQL does not safely remove enum labels in-place. Keep the
    # unused additive labels on downgrade; no table or financial row is rewritten.
