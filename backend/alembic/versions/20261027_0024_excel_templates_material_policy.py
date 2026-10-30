"""Versioned customer Excel templates and configurable small-order foam policy."""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0024"
down_revision = "20261027_0023"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "excel_templates" not in inspector.get_table_names():
        op.create_table(
            "excel_templates",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("account_key", sa.String(36), nullable=False),
            sa.Column("template_key", sa.String(128), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("confirmed", sa.Boolean(), nullable=False),
            sa.Column("confirmed_by", sa.String(36)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "account_key", "template_key", "version", name="uq_excel_template_version"
            ),
        )
        op.create_index("ix_excel_templates_account_key", "excel_templates", ["account_key"])
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE excel_templates ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON TABLE excel_templates FROM PUBLIC")
        for role in ("anon", "authenticated"):
            if connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                op.execute(f"REVOKE ALL ON TABLE excel_templates FROM {role}")
    if "foam_below_kg" not in {c["name"] for c in inspector.get_columns("products")}:
        op.add_column("products", sa.Column("foam_below_kg", sa.Numeric(12, 4), nullable=True))
    if "ck_product_foam_cutoff" not in {
        c["name"] for c in inspector.get_check_constraints("products")
    }:
        with op.batch_alter_table("products") as batch:
            batch.create_check_constraint(
                "ck_product_foam_cutoff",
                "foam_below_kg IS NULL OR (foam_below_kg > 0 AND foam_below_kg <= 10000)",
            )
    op.execute(
        sa.text(
            "UPDATE products SET foam_below_kg=1, packing_revision=packing_revision+1 WHERE foam_below_kg IS NULL AND packing_reviewed=true AND t_code IN ('11-034','11-035','11-169','11-398','16-035','16-108','16-159')"
        )
    )


def downgrade():
    with op.batch_alter_table("products") as batch:
        batch.drop_constraint("ck_product_foam_cutoff", type_="check")
        batch.drop_column("foam_below_kg")
    op.drop_table("excel_templates")
