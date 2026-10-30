import pytest
from pydantic import ValidationError
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.database import build_engine_options, normalize_database_url
from app.core.database_urls import migration_connect_args

TEST_URL = "postgresql://postgres.test:test@test.pooler.supabase.com:5432/postgres"


def test_supabase_postgres_url_uses_psycopg2_and_ssl() -> None:
    url = normalize_database_url(
        "postgres://postgres.example:secret@aws-0-region.pooler.supabase.com:5432/postgres"
    )

    assert url == (
        "postgresql+psycopg2://postgres.example:secret@"
        "aws-0-region.pooler.supabase.com:5432/postgres?sslmode=require"
    )


def test_existing_ssl_mode_is_preserved() -> None:
    url = normalize_database_url(
        "postgresql://postgres:secret@db.example.supabase.co:5432/postgres?sslmode=verify-full"
    )

    assert url.endswith("?sslmode=verify-full")


def test_supabase_direct_url_also_requires_ssl() -> None:
    url = normalize_database_url(
        "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
    )

    assert url.endswith("?sslmode=require")


def test_transaction_pooler_disables_client_side_pooling() -> None:
    options = build_engine_options(
        "postgresql://postgres.example:secret@aws-0-region.pooler.supabase.com:6543/postgres"
    )

    assert options["poolclass"] is NullPool


def test_migration_connections_bound_connect_lock_query_and_network_waits():
    options = migration_connect_args(TEST_URL)
    assert options["connect_timeout"] == 5
    assert options["tcp_user_timeout"] == 20000
    assert options["keepalives"] == 1
    assert "-c lock_timeout=5000" in options["options"]
    assert "-c statement_timeout=120000" in options["options"]


def test_migration_connections_preserve_existing_postgres_options():
    options = migration_connect_args(TEST_URL + "?options=-csearch_path%3Dexample")
    assert options["options"].startswith("-csearch_path=example ")


def test_sqlite_migrations_do_not_receive_postgres_connection_arguments():
    assert migration_connect_args("sqlite://") == {}


def test_database_configuration_requires_supabase_or_explicit_test_url(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_configuration_requires_https_public_app_url() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_APP_URL must use HTTPS"):
        Settings(
            _env_file=None,
            DATABASE_URL=TEST_URL,
            ENVIRONMENT="production",
            SECRET_KEY="a-unique-production-secret-that-is-long-enough",
            PUBLIC_APP_URL="http://app.example.com",
        )


def test_production_configuration_accepts_https_public_app_url() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL=TEST_URL,
        ENVIRONMENT="production",
        SECRET_KEY="a-unique-production-secret-that-is-long-enough",
        PUBLIC_APP_URL="https://app.example.com",
        FILE_STORAGE_PROVIDER="supabase",
        SUPABASE_URL="https://test.supabase.co",
        SUPABASE_STORAGE_KEY="sb_secret_disposable_test_only",
    )

    assert settings.PUBLIC_APP_URL == "https://app.example.com"


def test_invoice_bank_configuration_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="Invoice bank settings must provide"):
        Settings(
            _env_file=None,
            DATABASE_URL=TEST_URL,
            INVOICE_BANK_NAME="Approved Bank",
        )


def test_production_cannot_silently_fall_back_to_local_documents():
    with pytest.raises(ValidationError, match="private Supabase Storage"):
        Settings(_env_file=None, DATABASE_URL=TEST_URL, ENVIRONMENT="production",
            SECRET_KEY="a-unique-production-secret-that-is-long-enough", PUBLIC_APP_URL="https://app.example.com")


def test_storage_project_must_match_database_and_errors_hide_secret_inputs():
    with pytest.raises(ValidationError, match="same project") as error:
        Settings(_env_file=None, DATABASE_URL=TEST_URL, FILE_STORAGE_PROVIDER="supabase",
            SUPABASE_URL="https://another.supabase.co", SUPABASE_STORAGE_KEY="sb_secret_do_not_print_this")
    assert "sb_secret_do_not_print_this" not in str(error.value)
