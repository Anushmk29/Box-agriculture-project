"""Preserve ordered identity alongside buyer-approved fulfilled product identity."""

import sqlalchemy as sa

from alembic import op

revision = "20261026_0016"
down_revision = "20261025_0015"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("client_order_items")}
    foreign_keys = inspector.get_foreign_keys("client_order_items")
    with op.batch_alter_table("client_order_items") as batch:
        if "replacement_product_id" not in columns:
            batch.add_column(sa.Column("replacement_product_id", sa.String(36), nullable=True))
        if not any(fk["constrained_columns"] == ["replacement_product_id"] for fk in foreign_keys):
            batch.create_foreign_key("fk_order_item_replacement_product", "products",
                                     ["replacement_product_id"], ["id"])


def downgrade():
    # Downgrade would erase substitution provenance. Require an explicit recovery
    # decision instead of silently reverting a database containing fulfilled goods.
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT count(*) FROM client_order_items WHERE replacement_product_id IS NOT NULL"
    )).scalar():
        raise RuntimeError("Cannot remove substitution lineage while replacements exist")
    foreign_keys = sa.inspect(connection).get_foreign_keys("client_order_items")
    with op.batch_alter_table("client_order_items") as batch:
        for fk in foreign_keys:
            if fk["constrained_columns"] == ["replacement_product_id"] and fk["name"]:
                batch.drop_constraint(fk["name"], type_="foreignkey")
        batch.drop_column("replacement_product_id")
