"""Read-only inventory for a schema-preserving database reset."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.database import engine

with engine.connect() as connection:
    connection.execute(text("SET statement_timeout = '20s'"))
    tables = connection.execute(text("""
        SELECT schemaname, tablename FROM pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY schemaname, tablename
    """)).mappings().all()
    result = []
    for table in tables:
        schema, name = table['schemaname'], table['tablename']
        entry = dict(table)
        if schema in {'public', 'auth', 'storage'}:
            qualified = engine.dialect.identifier_preparer.quote_schema(schema) + '.' + engine.dialect.identifier_preparer.quote(name)
            entry['rows'] = connection.execute(text(f'SELECT count(*) FROM {qualified}')).scalar_one()
        result.append(entry)
    print(json.dumps(result, indent=2))
