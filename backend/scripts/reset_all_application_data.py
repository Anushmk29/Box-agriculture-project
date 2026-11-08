"""Back up and empty application tables without changing their schema."""
import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import make_url, text

from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, engine


def schema_snapshot(connection):
    queries = {
        'tables': "SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') ORDER BY c.relname",
        'columns': "SELECT table_name,column_name,ordinal_position,column_default,is_nullable,data_type,udt_name,is_identity,is_generated FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position",
        'constraints': "SELECT c.conname,c.conrelid::regclass::text AS table_name,pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' ORDER BY table_name,c.conname",
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname",
        'policies': "SELECT * FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
        'triggers': "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        'enums': "SELECT t.typname,e.enumlabel,e.enumsortorder FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY t.typname,e.enumsortorder",
    }
    return {key: [dict(row) for row in connection.execute(text(query)).mappings()] for key, query in queries.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    url = make_url(settings.DATABASE_URL)
    project = (url.username or '').removeprefix('postgres.') if 'pooler.supabase.com' in (url.host or '') else (url.host or '').removeprefix('db.').removesuffix('.supabase.co')
    if project != args.project_ref or not args.execute:
        raise SystemExit('Explicit execution flag and matching configured project reference required')

    quote = engine.dialect.identifier_preparer.quote
    backup_dir = Path(__file__).resolve().parents[2] / '.codex-artifact' / 'database-backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    backup_path = backup_dir / f'{project}-before-reset-{timestamp}.zip'

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        connection.execute(text("SET LOCAL idle_in_transaction_session_timeout = '180s'"))
        public_tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        tables = [table for table in public_tables if table != 'alembic_version']
        if set(tables) != set(Base.metadata.tables):
            raise RuntimeError('Database tables do not match the application models; reset aborted')
        targets = ', '.join('public.' + quote(table) for table in tables)
        # List every target explicitly; RESTRICT prevents deleting outside this set.
        connection.execute(text('LOCK TABLE ' + targets + ' IN ACCESS EXCLUSIVE MODE'))
        snapshot = schema_snapshot(connection)
        migration_rows = connection.execute(text('SELECT version_num FROM public.alembic_version ORDER BY version_num')).scalars().all()
        before = {table: connection.execute(text('SELECT count(*) FROM public.' + quote(table))).scalar_one() for table in tables}
        manifest = {
            'project_ref': project, 'created_at_utc': timestamp, 'row_counts': before,
            'migration_versions': migration_rows, 'schema_snapshot': snapshot,
            'format': 'One PostgreSQL COPY CSV file per table, including migration metadata. Contains confidential application data.',
        }
        cursor = connection.connection.driver_connection.cursor()
        try:
            with zipfile.ZipFile(backup_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))
                for table in public_tables:
                    output = io.StringIO()
                    cursor.copy_expert('COPY public.' + quote(table) + ' TO STDOUT WITH (FORMAT CSV, HEADER TRUE)', output)
                    archive.writestr('public/' + table + '.csv', output.getvalue())
        finally:
            cursor.close()
        with zipfile.ZipFile(backup_path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError('Backup integrity check failed; reset aborted')
            saved_manifest = json.loads(archive.read('manifest.json'))
            if saved_manifest['row_counts'] != before or len(archive.namelist()) != len(public_tables) + 1:
                raise RuntimeError('Backup content check failed; reset aborted')
        print(json.dumps({'backup': str(backup_path), 'tables': len(tables), 'rows_to_remove': sum(before.values())}), flush=True)

        connection.execute(text('TRUNCATE TABLE ' + targets + ' RESTART IDENTITY RESTRICT'))
        after = {table: connection.execute(text('SELECT count(*) FROM public.' + quote(table))).scalar_one() for table in tables}
        if any(after.values()) or schema_snapshot(connection) != snapshot:
            raise RuntimeError('Empty-table or schema verification failed; transaction rolled back')
        if connection.execute(text('SELECT version_num FROM public.alembic_version ORDER BY version_num')).scalars().all() != migration_rows:
            raise RuntimeError('Migration metadata changed; transaction rolled back')

    # Verify again on a new connection after commit.
    with engine.connect() as connection:
        final_counts = {table: connection.execute(text('SELECT count(*) FROM public.' + quote(table))).scalar_one() for table in tables}
        assert not any(final_counts.values()), 'Rows appeared after commit'
        assert schema_snapshot(connection) == snapshot, 'Schema changed after commit'
    report_path = backup_path.with_suffix('.report.json')
    report = {
        'project_ref': project, 'backup': str(backup_path),
        'backup_sha256': hashlib.sha256(backup_path.read_bytes()).hexdigest(),
        'cleared_tables': len(tables), 'rows_removed': sum(before.values()),
        'before_counts': before, 'after_counts': final_counts,
        'schema_preserved': True, 'migration_metadata_preserved': True,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
