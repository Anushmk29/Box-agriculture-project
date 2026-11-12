"""Reviewed packing history and source-linked fit feedback."""

import sqlalchemy as sa

from alembic import op

revision = "20261031_0025"
down_revision = "20261027_0024"
branch_labels = None
depends_on = None


def upgrade():
    existing = sa.inspect(op.get_bind()).get_table_names()
    if "packing_history_reviews" not in existing:
        op.create_table(
            "packing_history_reviews",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("example_id", sa.String(64), nullable=False),
            sa.Column("source_version", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("note", sa.String(2048), nullable=False),
            sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("example_id", name="uq_packing_history_example"),
            sa.CheckConstraint("revision > 0", name="ck_history_review_revision"),
            sa.CheckConstraint("status IN ('APPROVED', 'REJECTED')", name="ck_history_review_status"),
        )
        op.create_index("ix_packing_history_reviews_example_id", "packing_history_reviews", ["example_id"])
    if "packing_feedback" not in existing:
        op.create_table(
            "packing_feedback",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("box_id", sa.String(36), sa.ForeignKey("boxes.id"), nullable=False),
            sa.Column("packing_revision", sa.Integer(), nullable=False),
            sa.Column("outcome", sa.String(24), nullable=False),
            sa.Column("note", sa.String(2048), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("box_id", "packing_revision", "outcome", name="uq_packing_feedback_box_revision_outcome"),
            sa.CheckConstraint("outcome IN ('FIT', 'LARGER_BOX', 'EXCESS_SPACE', 'REPACKED', 'DAMAGE')", name="ck_packing_feedback_outcome"),
        )
        op.create_index("ix_packing_feedback_box_id", "packing_feedback", ["box_id"])
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        for table in ("packing_history_reviews", "packing_feedback"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
            for role in ("anon", "authenticated"):
                if connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                    op.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")


def downgrade():
    connection = op.get_bind()
    for table in ("packing_history_reviews", "packing_feedback"):
        if connection.scalar(sa.text(f"SELECT COUNT(*) FROM {table}")):
            raise RuntimeError("Packing evidence exists; preserve it before downgrading")
    op.drop_table("packing_feedback")
    op.drop_table("packing_history_reviews")
