"""Connection URL handling without loading application settings or an engine."""

from sqlalchemy import make_url


def normalize_database_url(database_url: str) -> str:
    """Return a psycopg2 SQLAlchemy URL for Supabase Database."""
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg2")
    if "supabase." in (url.host or "") and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return url.render_as_string(hide_password=False)


def migration_connect_args(database_url: str) -> dict:
    """Bound PostgreSQL migration waits without applying HTTP request budgets."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        return {}
    existing_options = url.query.get("options", "")
    return {
        "connect_timeout": 5,
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
        "tcp_user_timeout": 20000,
        "options": (
            f"{existing_options} -c lock_timeout=5000 -c statement_timeout=120000"
        ).strip(),
    }
