"""Backend-only durable request receipts for uncertain client outcomes."""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0020"
down_revision = "20261027_0019"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("request_results"):
        op.create_table("request_results",
            sa.Column("actor_id", sa.String(36), primary_key=True),
            sa.Column("operation", sa.String(64), primary_key=True),
            sa.Column("request_key", sa.String(128), primary_key=True),
            sa.Column("request_sha256", sa.String(64), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()))
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE request_results ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON TABLE request_results FROM PUBLIC")
        # Generic local PostgreSQL need not have Supabase's browser roles.
        for role in ("anon", "authenticated"):
            if connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                op.execute(f"REVOKE ALL ON TABLE request_results FROM {role}")
        # No client policies: only the existing trusted backend database role.


def downgrade():
    connection = op.get_bind()
    if sa.inspect(connection).has_table("request_results"):
        if connection.scalar(sa.text("SELECT 1 FROM request_results LIMIT 1")):
            raise RuntimeError("Cannot discard durable request receipts; keep replay protection during rollback")
        op.drop_table("request_results")
