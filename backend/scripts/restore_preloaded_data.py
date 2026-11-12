"""Restore identified preload cohorts while preserving current client data."""
import argparse
import csv
import io
import json
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, make_url, text

from app import models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, normalize_database_url

MASTER_TABLES = ['users', 'box_catalogue', 'products', 'customers', 'price_lists', 'customer_selling_prices', 'client_sku_mappings']
CUSTOMER_COHORTS = {'2026-09-04 12:16', '2026-09-08 13:01', '2026-09-14 08:58', '2026-09-14 11:28', '2026-09-17 10:44'}
PRICE_COHORTS = {'2026-09-14 11:28', '2026-09-25 06:32'}
NORWAY_CODES = {'11473','11474','11532','11320','11322','11475','11015','11476','11324','11323','11028','11519','11520','11521','11372','11531','11523','11374','11477','11478'}


def rows(content):
    return list(csv.DictReader(io.StringIO(content)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--project-ref', required=True)
    args = parser.parse_args()
    if make_url(settings.DATABASE_URL).username != 'postgres.' + args.project_ref:
        raise SystemExit('Configured project does not match requested project')
    with zipfile.ZipFile(args.backup) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if manifest['project_ref'] != args.project_ref:
            raise SystemExit('Backup project mismatch')
        contents = {table: archive.read('public/' + table + '.csv').decode() for table in MASTER_TABLES}
    saved = {table: rows(content) for table, content in contents.items()}
    saved_by_id = {table:{row['id']:row for row in data} for table,data in saved.items()}
    selected = {table: list(data) for table, data in saved.items()}
    selected['customers'] = [r for r in saved['customers'] if r['created_at'][:16] in CUSTOMER_COHORTS]
    customer_ids = {r['id'] for r in selected['customers']}
    selected['customer_selling_prices'] = [r for r in saved['customer_selling_prices'] if r['created_at'][:16] in PRICE_COHORTS and r['main_customer_id'] in customer_ids and (not r['sub_customer_id'] or r['sub_customer_id'] in customer_ids)]
    selected['client_sku_mappings'] = [r for r in saved['client_sku_mappings'] if r['client_sku_code'] in NORWAY_CODES and r['main_customer_id'] in customer_ids]
    keys = {
        'users': ['email'], 'box_catalogue': ['box_type','size_name','length_cm','width_cm','height_cm'],
        'products': ['t_code'], 'customers': ['client_code'],
        'price_lists': ['product_id','customer_id','pack_size','valid_from'],
        'customer_selling_prices': ['main_customer_id','sub_customer_id','product_id','pack_size','valid_from'],
        'client_sku_mappings': ['main_customer_id','client_sku_code','client_raw_name'],
    }
    engine = create_engine(normalize_database_url(settings.DATABASE_URL), connect_args={'connect_timeout':10})
    quote = engine.dialect.identifier_preparer.quote
    backup_dir = Path(__file__).resolve().parents[2] / '.codex-artifact' / 'database-backups'
    before_path = backup_dir / (args.project_ref + '-before-preload-restore-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '.zip')
    inserted, remaps, expected = {}, {}, {}
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout='10s'"))
        connection.execute(text("SET LOCAL statement_timeout='60s'"))
        connection.execute(text('LOCK TABLE ' + ', '.join('public.' + quote(t) for t in MASTER_TABLES) + ' IN EXCLUSIVE MODE'))
        cursor = connection.connection.driver_connection.cursor()

        def export(table):
            output = io.StringIO()
            cursor.copy_expert('COPY public.' + quote(table) + ' TO STDOUT WITH (FORMAT CSV, HEADER TRUE)', output)
            return output.getvalue()

        try:
            before = {table: export(table) for table in sorted(Base.metadata.tables)}
            with zipfile.ZipFile(before_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json', json.dumps({'project_ref':args.project_ref,'purpose':'Current client data before preload restoration'}))
                for table, content in before.items():
                    archive.writestr('public/' + table + '.csv', content)
            with zipfile.ZipFile(before_path) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError('Current data backup failed verification')

            for table in MASTER_TABLES:
                current = rows(before[table])
                temp = 'preload_' + table
                connection.execute(text('CREATE TEMP TABLE ' + quote(temp) + ' (LIKE public.' + quote(table) + ') ON COMMIT DROP'))
                cursor.copy_expert('COPY ' + quote(temp) + ' FROM STDIN WITH (FORMAT CSV, HEADER TRUE)', io.StringIO(contents[table]))
                def natural(row, key_columns=keys[table]):
                    return tuple(row[column] for column in key_columns)
                existing = {natural(r):r for r in current}
                mapping = {}
                additions = []
                for source in selected[table]:
                    item = dict(source)
                    for column, target in [('product_id','products'),('customer_id','customers'),('main_customer_id','customers'),('sub_customer_id','customers'),('parent_customer_id','customers')]:
                        if column in item and item[column]:
                            if item[column] not in remaps.get(target, {}):
                                # Self references resolve after all customers are mapped below.
                                if table == 'customers' and column == 'parent_customer_id':
                                    continue
                                raise RuntimeError('Unresolved preload dependency in ' + table)
                            item[column] = remaps[target][item[column]]
                    match = existing.get(natural(item))
                    if match:
                        mapping[source['id']] = match['id']
                        continue
                    if table == 'users':
                        item['id'] = str(uuid.uuid4())
                    if any(r['id'] == item['id'] for r in current):
                        raise RuntimeError('Conflicting record ID in ' + table)
                    mapping[source['id']] = item['id']
                    existing[natural(item)] = item
                    additions.append((source['id'], item))
                if table == 'customers':
                    for _, item in additions:
                        if item['parent_customer_id']:
                            item['parent_customer_id'] = mapping[item['parent_customer_id']]
                remaps[table] = mapping
                changes = {}
                for source_id, item in additions:
                    for column,value in item.items():
                        if value != saved_by_id[table][source_id][column]:
                            changes.setdefault(column,[]).append((source_id,value))
                # Update references in batches instead of one network round-trip per price.
                for column, pairs in changes.items():
                    for offset in range(0,len(pairs),500):
                        batch = pairs[offset:offset+500]
                        placeholders = ', '.join('(:old'+str(i)+', :new'+str(i)+')' for i in range(len(batch)))
                        params = {key:value for i,(old,new) in enumerate(batch) for key,value in [('old'+str(i),old),('new'+str(i),new)]}
                        connection.execute(text('UPDATE ' + quote(temp) + ' target SET ' + quote(column) + '=mapping.new_id FROM (VALUES ' + placeholders + ') mapping(old_id,new_id) WHERE target.id=mapping.old_id'), params)
                new_ids = [item['id'] for _,item in additions]
                if new_ids:
                    # Select original CSV rows from the typed temp table, preserving NULLs.
                    placeholders = ', '.join(':id' + str(i) for i in range(len(new_ids)))
                    connection.execute(text('INSERT INTO public.' + quote(table) + ' SELECT * FROM ' + quote(temp) + ' WHERE id IN (' + placeholders + ')'), {'id'+str(i):value for i,value in enumerate(new_ids)})
                inserted[table] = len(new_ids)
                expected[table] = {r['id']:r for r in current}
                expected[table].update({item['id']:item for _,item in additions})
                actual = {r['id']:r for r in rows(export(table))}
                if actual != expected[table]:
                    raise RuntimeError('Restored data mismatch in ' + table)
                print(json.dumps({'table':table,'restored':len(new_ids),'existing_preserved':len(current)}),flush=True)
            for table in set(Base.metadata.tables) - set(MASTER_TABLES):
                if {r['id']:r for r in rows(export(table))} != {r['id']:r for r in rows(before[table])}:
                    raise RuntimeError('Client workflow records changed: ' + table)
        finally:
            cursor.close()
    with engine.connect() as connection:
        for table in MASTER_TABLES:
            actual = connection.execute(text('SELECT count(*) FROM public.' + quote(table))).scalar_one()
            if actual != len(expected[table]):
                raise RuntimeError('Post-commit count mismatch in ' + table)
    report = {'restored':inserted,'current_client_data_preserved':True,'historical_transactions_restored':False,
              'excluded_backup_rows':{table:len(saved[table])-len(selected[table]) for table in MASTER_TABLES}, 'backup_before_restore':str(before_path)}
    before_path.with_suffix('.report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
