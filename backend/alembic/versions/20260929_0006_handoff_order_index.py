"""Index Handoff 02 contracts by client order so invoicing stops scanning every handoff."""

import sqlalchemy as sa

from alembic import op

revision = "20260929_0006"
down_revision = "20260928_0005"
branch_labels = None
depends_on = None

INDEX = "ix_agent_handoffs_client_order_id"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    # 0001 builds tables from current models, so fresh databases already have the column.
    if "client_order_id" not in {c["name"] for c in _inspector().get_columns("agent_handoffs")}:
        op.add_column(
            "agent_handoffs",
            sa.Column(
                "client_order_id", sa.String(36), sa.ForeignKey("client_orders.id"), nullable=True
            ),
        )
    if INDEX not in {i["name"] for i in _inspector().get_indexes("agent_handoffs")}:
        op.create_index(INDEX, "agent_handoffs", ["client_order_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE agent_handoffs
            SET client_order_id = payload_json ->> 'final_packing_manifest_id'
            WHERE handoff_type = 'HANDOFF_02_FINAL_PACKING'
              AND client_order_id IS NULL
              AND payload_json ->> 'final_packing_manifest_id' IN (SELECT id FROM client_orders)
            """
        )


def downgrade() -> None:
    if INDEX in {i["name"] for i in _inspector().get_indexes("agent_handoffs")}:
        op.drop_index(INDEX, table_name="agent_handoffs")
    if "client_order_id" in {c["name"] for c in _inspector().get_columns("agent_handoffs")}:
        op.drop_column("agent_handoffs", "client_order_id")
