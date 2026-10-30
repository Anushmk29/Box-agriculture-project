import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from jwt import InvalidTokenError

from app.core.config import settings

password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def password_needs_rehash(encoded: str) -> bool:
    if not encoded.startswith("$argon2"):
        return True
    try:
        return password_hasher.check_needs_rehash(encoded)
    except InvalidHashError:
        return True


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        plain_password.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if hashed_password.startswith("$argon2"):
        try:
            return password_hasher.verify(hashed_password, plain_password)
        except (InvalidHashError, VerificationError):
            return False
    if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
        except ValueError:
            return False
    legacy_salt = settings.SECRET_KEY[:16]
    legacy_hash = hashlib.sha256((plain_password + legacy_salt).encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy_hash, hashed_password)


def create_access_token(data: dict[str, Any], expires_delta: int | None = None) -> str:
    to_encode = data.copy()
    lifetime = expires_delta or settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    to_encode.update({"exp": datetime.now(UTC) + timedelta(seconds=lifetime)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except InvalidTokenError:
        return None
