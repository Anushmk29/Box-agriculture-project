"""Shared login attempt limits for public authentication."""

import sqlalchemy as sa

from alembic import op

revision = "20261108_0026"
down_revision = "20261031_0025"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if "login_limits" not in sa.inspect(connection).get_table_names():
        op.create_table(
            "login_limits",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("window_start", sa.BigInteger(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.CheckConstraint("attempts > 0", name="ck_login_limit_attempts"),
        )
        op.create_index("ix_login_limits_window_start", "login_limits", ["window_start"])
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE login_limits ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON TABLE login_limits FROM PUBLIC")
        for role in ("anon", "authenticated"):
            if connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                op.execute(f"REVOKE ALL ON TABLE login_limits FROM {role}")


def downgrade():
    op.drop_table("login_limits")
