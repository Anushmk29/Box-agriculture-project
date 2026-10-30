"""Support vegetables, spices, packaged food and personal care products."""
from alembic import op

revision = "20261006_0009"
down_revision = "20261002_0008"
branch_labels = None
depends_on = None

CATEGORIES = ("VEGETABLE", "SPICE", "PACKAGED_FOOD", "PERSONAL_CARE", "OTHER")


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        for category in CATEGORIES:
            op.execute(f"ALTER TYPE public.producetype ADD VALUE IF NOT EXISTS '{category}'")


def downgrade():
    raise RuntimeError("Category values may be in use; removing them requires a reviewed data migration")
