"""Restore only the previous admin's credentials after a complete data reset."""
import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text, make_url
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User, UserRole


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--project-ref', required=True)
    args = parser.parse_args()
    url = make_url(settings.DATABASE_URL)
    if url.username != 'postgres.' + args.project_ref:
        raise SystemExit('Configured database does not match the requested project')
    with zipfile.ZipFile(args.backup) as backup:
        manifest = json.loads(backup.read('manifest.json'))
        if manifest['project_ref'] != args.project_ref:
            raise SystemExit('Backup belongs to a different project')
        users = list(csv.DictReader(io.StringIO(backup.read('public/users.csv').decode('utf-8'))))
    admins = [user for user in users if user['role'] == 'OWNER_ADMIN' and user['is_active'] == 't' and user['is_service_principal'] == 'f']
    if len(admins) != 1:
        raise SystemExit('Expected exactly one active human admin in the backup')
    saved = admins[0]
    with SessionLocal.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.execute(text('LOCK TABLE public.users IN EXCLUSIVE MODE'))
        if db.query(User).count() != 0:
            raise SystemExit('Users table is not empty; refusing to overwrite accounts')
        user = User(
            email=saved['email'], full_name=saved['full_name'],
            password_hash=saved['password_hash'], role=UserRole.OWNER_ADMIN,
            is_active=True, is_service_principal=False,
            must_change_password=saved['must_change_password'] == 't',
        )
        # A fresh user ID keeps pre-reset tokens invalid.
        db.add(user)
        db.flush()
        assert user.id != saved['id']
    print(json.dumps({'restored_admin': saved['email'], 'password': 'unchanged', 'old_sessions': 'remain invalid', 'business_data_restored': False}))


if __name__ == '__main__':
    main()
