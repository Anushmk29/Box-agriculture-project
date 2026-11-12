"""Restore the product/SKU catalogue from a pre-reset backup, without other data."""
import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, make_url, text

from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, normalize_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--project-ref', required=True)
    args = parser.parse_args()
    if make_url(settings.DATABASE_URL).username != 'postgres.' + args.project_ref:
        raise SystemExit('Configured database does not match requested project')
    with zipfile.ZipFile(args.backup) as backup:
        manifest = json.loads(backup.read('manifest.json'))
        if manifest['project_ref'] != args.project_ref:
            raise SystemExit('Backup belongs to a different project')
        content = backup.read('public/products.csv').decode('utf-8')
    expected = {row['id']: row for row in csv.DictReader(io.StringIO(content))}
    if len(expected) != manifest['row_counts']['products']:
        raise SystemExit('Product backup row count does not match the manifest')
    columns = next(csv.reader(io.StringIO(content)))
    engine = create_engine(normalize_database_url(settings.DATABASE_URL), connect_args={'connect_timeout': 10})
    quote = engine.dialect.identifier_preparer.quote

    def counts(connection):
        query = ' UNION ALL '.join(
            "SELECT '" + name + "' AS name, count(*) AS rows FROM public." + quote(name)
            for name in sorted(Base.metadata.tables) if name != 'products'
        )
        return {row.name: row.rows for row in connection.execute(text(query))}

    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        connection.execute(text('LOCK TABLE public.products IN EXCLUSIVE MODE'))
        current_columns = connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='products' ORDER BY ordinal_position")).scalars().all()
        if current_columns != columns:
            raise SystemExit('Product schema differs from the backup')
        before_other = counts(connection)
        cursor = connection.connection.driver_connection.cursor()
        try:
            previous = io.StringIO()
            cursor.copy_expert('COPY public.products TO STDOUT WITH (FORMAT CSV, HEADER TRUE)', previous)
            previous_rows = {row['id']: row for row in csv.DictReader(io.StringIO(previous.getvalue()))}
            current_codes = {row['t_code'] for row in previous_rows.values()}
            missing = {key: row for key, row in expected.items() if row['t_code'] not in current_codes}
            if set(missing) & set(previous_rows):
                raise RuntimeError('Backup IDs conflict with existing product IDs; restoration aborted')
            connection.execute(text('CREATE TEMP TABLE restore_products_buffer (LIKE public.products) ON COMMIT DROP'))
            cursor.copy_expert(
                'COPY restore_products_buffer (' + ', '.join(quote(column) for column in columns) + ') FROM STDIN WITH (FORMAT CSV, HEADER TRUE)',
                io.StringIO(content),
            )
            column_sql = ', '.join(quote(column) for column in columns)
            connection.execute(text(
                'INSERT INTO public.products (' + column_sql + ') SELECT ' +
                ', '.join('saved.' + quote(column) for column in columns) +
                ' FROM restore_products_buffer saved WHERE NOT EXISTS '
                '(SELECT 1 FROM public.products current WHERE current.t_code = saved.t_code)'
            ))
            restored = io.StringIO()
            cursor.copy_expert('COPY public.products TO STDOUT WITH (FORMAT CSV, HEADER TRUE)', restored)
        finally:
            cursor.close()
        actual = {row['id']: row for row in csv.DictReader(io.StringIO(restored.getvalue()))}
        if actual != {**previous_rows, **missing} or counts(connection) != before_other:
            raise RuntimeError('Restoration verification failed; rolling back')

    with engine.connect() as connection:
        total = connection.execute(text('SELECT count(*) FROM public.products')).scalar_one()
        assert total == len(previous_rows) + len(missing)
        assert counts(connection) == before_other
    print(json.dumps({'products_restored': len(missing), 'existing_products_preserved': len(previous_rows), 'total_products': total, 'all_product_fields_verified': True, 'other_tables_unchanged': True}))


if __name__ == '__main__':
    main()
