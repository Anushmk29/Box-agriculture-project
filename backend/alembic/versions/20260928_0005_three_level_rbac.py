"""Replace legacy human roles with the three-level RBAC model."""

import sqlalchemy as sa

from alembic import op

revision = "20260928_0005"
down_revision = "20260917_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "must_change_password" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}:
        op.add_column(
            "users",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if op.get_bind().dialect.name != "postgresql":
        op.execute(
            """
            UPDATE users SET role = CASE role
                WHEN 'ACCOUNTING_OFFICER' THEN 'GENERAL_ACCOUNTING'
                WHEN 'SYSTEM_ADMIN' THEN 'OWNER_ADMIN'
                WHEN 'AI_AGENT_1' THEN 'AI_AGENT_1'
                WHEN 'AI_AGENT_2' THEN 'AI_AGENT_2'
                ELSE 'GENERAL'
            END
            """
        )
        return

    # The bootstrap migration uses current models on a fresh database.
    labels = set(op.get_bind().execute(sa.text(
        "SELECT enumlabel FROM pg_enum JOIN pg_type ON enumtypid = pg_type.oid "
        "WHERE pg_type.oid = 'userrole'::regtype"
    )).scalars())
    if labels == {"GENERAL", "GENERAL_ACCOUNTING", "OWNER_ADMIN", "AI_AGENT_1", "AI_AGENT_2"}:
        return
    op.execute("ALTER TYPE userrole RENAME TO userrole_legacy")
    op.execute(
        "CREATE TYPE userrole AS ENUM "
        "('GENERAL', 'GENERAL_ACCOUNTING', 'OWNER_ADMIN', 'AI_AGENT_1', 'AI_AGENT_2')"
    )
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE users ALTER COLUMN role TYPE userrole USING (
            CASE role::text
                WHEN 'ACCOUNTING_OFFICER' THEN 'GENERAL_ACCOUNTING'
                WHEN 'SYSTEM_ADMIN' THEN 'OWNER_ADMIN'
                WHEN 'AI_AGENT_1' THEN 'AI_AGENT_1'
                WHEN 'AI_AGENT_2' THEN 'AI_AGENT_2'
                ELSE 'GENERAL'
            END
        )::userrole
        """
    )
    op.execute("DROP TYPE userrole_legacy")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE userrole RENAME TO userrole_three_level")
        op.execute(
            "CREATE TYPE userrole AS ENUM "
            "('SYSTEM_ADMIN', 'STAFF', 'CUSTOMER_AGENT', 'PROCUREMENT_BUYER', "
            "'FLOOR_STAFF', 'OPERATIONS_MANAGER', 'ACCOUNTING_OFFICER', "
            "'AI_AGENT_1', 'AI_AGENT_2')"
        )
        op.execute(
            """
            ALTER TABLE users ALTER COLUMN role TYPE userrole USING (
                CASE role::text
                    WHEN 'GENERAL_ACCOUNTING' THEN 'ACCOUNTING_OFFICER'
                    WHEN 'OWNER_ADMIN' THEN 'SYSTEM_ADMIN'
                    WHEN 'AI_AGENT_1' THEN 'AI_AGENT_1'
                    WHEN 'AI_AGENT_2' THEN 'AI_AGENT_2'
                    ELSE 'STAFF'
                END
            )::userrole
            """
        )
        op.execute("DROP TYPE userrole_three_level")
    else:
        op.execute(
            """
            UPDATE users SET role = CASE role
                WHEN 'GENERAL_ACCOUNTING' THEN 'ACCOUNTING_OFFICER'
                WHEN 'OWNER_ADMIN' THEN 'SYSTEM_ADMIN'
                WHEN 'AI_AGENT_1' THEN 'AI_AGENT_1'
                WHEN 'AI_AGENT_2' THEN 'AI_AGENT_2'
                ELSE 'STAFF'
            END
            """
        )
    op.drop_column("users", "must_change_password")
