"""Add Phase 4 payment and receipt integrity fields."""

import sqlalchemy as sa

from alembic import op

revision = "20260910_0003"
down_revision = "20260909_0002"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "payment_mode" not in _columns("payment_transactions"):
        op.add_column("payment_transactions", sa.Column("payment_mode", sa.String(32)))
    if "payload_sha256" not in _columns("receipts"):
        op.add_column("receipts", sa.Column("payload_sha256", sa.String(64)))
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("payment_transactions")
    }
    if "ix_payment_transactions_bank_wire_reference" not in indexes:
        op.create_index(
            "ix_payment_transactions_bank_wire_reference",
            "payment_transactions",
            ["bank_wire_reference"],
            unique=True,
        )


def downgrade() -> None:
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("payment_transactions")
    }
    if "ix_payment_transactions_bank_wire_reference" in indexes:
        op.drop_index(
            "ix_payment_transactions_bank_wire_reference",
            table_name="payment_transactions",
        )
    if "payload_sha256" in _columns("receipts"):
        op.drop_column("receipts", "payload_sha256")
    if "payment_mode" in _columns("payment_transactions"):
        op.drop_column("payment_transactions", "payment_mode")
