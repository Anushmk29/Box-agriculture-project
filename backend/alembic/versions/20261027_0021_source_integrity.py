"""Record immutable source integrity without rewriting legacy paths."""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0021"
down_revision = "20261027_0020"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("raw_order_files")}
    for name, type_ in (("file_sha256", sa.String(64)), ("file_size_bytes", sa.BigInteger())):
        if name not in columns:
            op.add_column("raw_order_files", sa.Column(name, type_, nullable=True))
    if "ck_raw_file_integrity_metadata" not in {item["name"] for item in sa.inspect(connection).get_check_constraints("raw_order_files")}:
        with op.batch_alter_table("raw_order_files") as batch:
            batch.create_check_constraint("ck_raw_file_integrity_metadata",
                "(file_sha256 IS NULL AND file_size_bytes IS NULL) OR "
                "(file_sha256 IS NOT NULL AND length(file_sha256) = 64 AND file_size_bytes IS NOT NULL AND file_size_bytes > 0 AND file_size_bytes <= 26214400)")
    if "ix_raw_order_files_storage_url" not in {item["name"] for item in sa.inspect(connection).get_indexes("raw_order_files")}:
        op.create_index("ix_raw_order_files_storage_url", "raw_order_files", ["storage_url"])


def downgrade():
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("raw_order_files")}
    if "file_sha256" in columns and connection.scalar(sa.text(
            "SELECT 1 FROM raw_order_files WHERE file_sha256 IS NOT NULL LIMIT 1")):
        raise RuntimeError("Cannot discard source integrity metadata; retain it during rollback")
    if "ix_raw_order_files_storage_url" in {item["name"] for item in sa.inspect(connection).get_indexes("raw_order_files")}:
        op.drop_index("ix_raw_order_files_storage_url", table_name="raw_order_files")
    with op.batch_alter_table("raw_order_files") as batch:
        if "ck_raw_file_integrity_metadata" in {item["name"] for item in sa.inspect(connection).get_check_constraints("raw_order_files")}:
            batch.drop_constraint("ck_raw_file_integrity_metadata", type_="check")
        for name in ("file_size_bytes", "file_sha256"):
            if name in columns:
                batch.drop_column(name)
