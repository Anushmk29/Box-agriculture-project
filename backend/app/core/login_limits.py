"""Reserve login attempts atomically, independently of a failed login's rollback."""

import hashlib
import hmac
import time

from fastapi import HTTPException
from sqlalchemy import case, delete, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.models.login_limit import LoginLimit

WINDOW_SECONDS = 15 * 60
ACCOUNT_ATTEMPTS = 10
IP_ATTEMPTS = 50


def enforce_login_limit(db, email: str, client_host: str):
    now = int(time.time())
    window = now - now % WINDOW_SECONDS
    counters = []
    for scope, value, limit in (("account", email.strip().lower(), ACCOUNT_ATTEMPTS),
                                ("ip", client_host, IP_ATTEMPTS)):
        key = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{value}".encode(), hashlib.sha256).hexdigest()
        counters.append((key, limit))
    blocked = False
    try:
        # These reservations must survive the request transaction's 401 rollback.
        with db.get_bind().begin() as connection:
            dialect = connection.dialect.name
            if dialect not in {"postgresql", "sqlite"}:
                raise RuntimeError("Login limits require PostgreSQL or SQLite")
            if dialect == "postgresql":
                connection.execute(text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            connection.execute(delete(LoginLimit).where(LoginLimit.window_start < window))
            insert = postgres_insert if dialect == "postgresql" else sqlite_insert
            for key, limit in sorted(counters):
                count = connection.execute(insert(LoginLimit).values(key=key, window_start=window, attempts=1)
                    .on_conflict_do_update(index_elements=["key"], set_={
                        "window_start": window,
                        "attempts": case(
                            (LoginLimit.window_start != window, 1),
                            (LoginLimit.attempts < limit + 1, LoginLimit.attempts + 1),
                            else_=LoginLimit.attempts),
                    }).returning(LoginLimit.attempts)).scalar_one()
                blocked = blocked or count > limit
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Login is temporarily unavailable. Please retry shortly.",
                            headers={"Retry-After": "30"}) from exc
    if blocked:
        raise HTTPException(429, "Too many login attempts. Please try again later.",
                            headers={"Retry-After": str(window + WINDOW_SECONDS - now)})
