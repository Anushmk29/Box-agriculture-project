import hashlib

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.user import User, UserRole


@pytest.mark.parametrize("password", ["a" * 73, "ก" * 128, "🌾" * 128])
def test_long_passwords_use_argon2_without_truncation(password):
    encoded = hash_password(password)
    assert encoded.startswith("$argon2id$")
    assert len(encoded) <= 255
    assert verify_password(password, encoded)
    assert not verify_password(password[:-1] + "X", encoded)


def test_existing_bcrypt_and_legacy_digest_passwords_still_verify():
    password = "Legacy-password-123!"
    bcrypt_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    digest_hash = hashlib.sha256((password + settings.SECRET_KEY[:16]).encode()).hexdigest()
    for encoded in [bcrypt_hash, digest_hash]:
        assert verify_password(password, encoded)
        assert not verify_password("wrong", encoded)
    assert not verify_password("a" * 73, bcrypt_hash)
    assert not verify_password(password, "$argon2id$malformed")


@pytest.fixture()
def legacy_login(db_session):
    password = "Legacy-password-123!"
    user = User(email="legacy-login@example.invalid", full_name="Legacy Login", role=UserRole.GENERAL,
                password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
    db_session.add(user)
    db_session.commit()
    original = app.dependency_overrides.copy()
    app.dependency_overrides.clear()

    def transaction():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = transaction
    try:
        yield TestClient(app, raise_server_exceptions=False), user, password
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


def test_successful_legacy_login_upgrades_hash_without_changing_session_version(legacy_login):
    client, user, password = legacy_login
    response = client.post("/api/v3/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 200
    assert user.password_hash.startswith("$argon2id$")
    assert user.session_version == 0
    assert client.get("/api/v3/auth/me").status_code == 200


def test_failed_legacy_login_does_not_change_hash(legacy_login):
    client, user, _ = legacy_login
    before = user.password_hash
    assert client.post("/api/v3/auth/login", json={"email": user.email, "password": "wrong"}).status_code == 401
    assert user.password_hash == before


def test_legacy_upgrade_rechecks_password_after_acquiring_user_lock(legacy_login, monkeypatch):
    from app.api import auth

    client, user, password = legacy_login
    before = user.password_hash
    checks = []

    def changed_while_waiting(*args):
        checks.append(True)
        return len(checks) == 1

    monkeypatch.setattr(auth, "verify_password", changed_while_waiting)
    response = client.post("/api/v3/auth/login", json={"email": user.email, "password": password})
    assert response.status_code == 401
    assert len(checks) == 2
    assert user.password_hash == before
