import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Placeholder so the app engine can be built; tests never connect to it.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres.test:test@test.pooler.supabase.com:5432/postgres"
)

from app import models  # noqa: E402, F401 - registers every model with Base
from app.core.database import Base


@pytest.fixture()
def db_session(monkeypatch, tmp_path):
    # Existing regression fixtures exercise the legacy rollout setting.
    # New profile tests explicitly enable strict reviewed packing.
    from app.core.config import settings
    monkeypatch.setattr(settings, "PACKING_PROFILES_ENABLED", False)
    # Historical synthetic workbooks exercise the legacy adapter. Recurring-format
    # integration tests explicitly enable the new default-on Excel rollout.
    monkeypatch.setattr(settings, "EXCEL_TEMPLATE_PROFILES_ENABLED", False)
    monkeypatch.setattr(settings, "FILE_STORAGE_PROVIDER", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "LEGACY_UPLOAD_ROOT", str(tmp_path))
    # In-memory throwaway DB for unit tests; never touches Supabase.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
