import uuid
from typing import Any

from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.core import request_limits
from app.core.config import settings
from app.core.database_urls import normalize_database_url as normalize_database_url


def uuid_pk() -> str:
    """Default factory for String(36) primary key columns."""
    return str(uuid.uuid4())


def build_engine_options(database_url: str) -> dict[str, Any]:
    """Choose safe connection settings for Supabase pooler modes."""
    url = make_url(database_url)
    connection_options = {"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10,
                          "keepalives_interval": 5, "keepalives_count": 3, "tcp_user_timeout": 20000}
    if url.port == 6543 and "supabase." in (url.host or ""):
        return {"poolclass": NullPool, "connect_args": connection_options}

    return {"pool_size": 5, "max_overflow": 5, "pool_recycle": 300, "pool_timeout": 5,
            "connect_args": connection_options}


database_url = normalize_database_url(settings.DATABASE_URL)

engine = create_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    **build_engine_options(database_url),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """One transaction; every HTTP caller must use Depends(..., scope="function").

    FastAPI then serializes the response and exits this dependency before sending
    HTTP headers. Routes and modules only flush; this is the sole commit owner.
    Background tasks/stream producers must open their own sessions.
    """
    db = SessionLocal()
    try:
        if isinstance(db, request_limits.Session):
            db.info["request_deadline"] = request_limits.monotonic() + request_limits.REQUEST_SECONDS
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
