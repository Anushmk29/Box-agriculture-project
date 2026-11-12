"""Bounded dummy-data regressions for the November security hardening release."""

import asyncio
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from test_pricing import _setup

from app.api.order_templates import validate_definition
from app.core.body_limits import BodyLimits
from app.core.config import Settings
from app.core.security import verify_password
from app.models.user import User, UserRole
from app.services.excel_profiles import references
from app.services.name_matcher import NameFirstMatcher


def script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("environment", ["development", "test", "production"])
@pytest.mark.parametrize("password", ["", "short", "x" * 129, "Development-Only-Change-Me!",
                                      "replace-with-a-one-time-random-password"])
def test_seed_refuses_missing_or_public_password_before_database_work(monkeypatch, environment, password):
    seed = script("seed_master_data")
    monkeypatch.setattr(seed.settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(seed.settings, "SEED_INITIAL_PASSWORD", SecretStr(password))
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid seed credentials must fail before database access")
    monkeypatch.setattr(seed.Base.metadata, "create_all", forbidden)
    monkeypatch.setattr(seed, "SessionLocal", forbidden)
    with pytest.raises(RuntimeError, match="explicit unique password"):
        seed.seed_all()


@pytest.mark.parametrize("existing_owner", [False, True])
def test_dotenv_only_production_seed_uses_explicit_secret_and_preserves_existing_owner(
    monkeypatch, tmp_path, db_session, existing_owner,
):
    seed = script("seed_master_data")
    for key in ("ENVIRONMENT", "SEED_INITIAL_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    password = "Dummy-one-time-seed-secret-42!"
    dotenv = tmp_path / "seed.env"
    dotenv.write_text(f"ENVIRONMENT=production\nSEED_INITIAL_PASSWORD={password}\n", encoding="utf-8")
    configured = Settings(_env_file=dotenv,
        DATABASE_URL="postgresql://postgres.dummy:dummy@dummy.pooler.supabase.com:5432/postgres",
        SECRET_KEY="dummy-production-signing-secret-for-local-test-only",
        PUBLIC_APP_URL="https://dummy.example", FILE_STORAGE_PROVIDER="supabase",
        SUPABASE_URL="https://dummy.supabase.co", SUPABASE_STORAGE_KEY="sb_secret_dummy_only")
    assert password not in repr(configured)
    existing = User(email="admin@agricultureislife.com", full_name="Disabled dummy owner",
                    password_hash="preserve-dummy-hash", role=UserRole.OWNER_ADMIN,
                    is_active=False, must_change_password=True, session_version=7)
    if existing_owner:
        db_session.add(existing)
        db_session.flush()
    monkeypatch.setattr(seed, "settings", configured)
    monkeypatch.setattr(seed.Base.metadata, "create_all", lambda **kwargs: None)
    monkeypatch.setattr(seed, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    class SeededUsers(Exception):
        pass
    def stop_after_user_flush():
        db_session.flush()
        raise SeededUsers
    monkeypatch.setattr(db_session, "commit", stop_after_user_flush)
    with pytest.raises(SeededUsers):
        seed.seed_all()
    if existing_owner:
        assert (existing.password_hash, existing.is_active, existing.session_version) == ("preserve-dummy-hash", False, 7)
    created = [user for user in db_session.query(User).all() if not existing_owner or user.id != existing.id]
    assert len(created) == (8 if existing_owner else 9)
    assert all(verify_password(password, user.password_hash) for user in created)
    assert all(not verify_password("Development-Only-Change-Me!", user.password_hash) for user in created)
    assert all(user.must_change_password == (not user.is_service_principal) for user in created)


@pytest.mark.parametrize("types", [
    [b"multipart/form-data; boundary=dummy", b"application/octet-stream"],
    [b"application/octet-stream", b"multipart/form-data; boundary=dummy"],
    [b"application/json", b"application/json"],
])
def test_duplicate_content_types_reject_before_reading_or_parser_admission(types):
    async def check():
        calls, sent = [], []
        async def downstream(*args):
            calls.append("parser")
        async def receive():
            pytest.fail("Duplicate headers must be rejected before reading the body")
        async def send(message):
            sent.append(message)
        limited = BodyLimits(downstream)
        await limited.upload_slots.acquire()
        await limited.upload_slots.acquire()
        await limited({"type": "http", "headers": [(b"content-type", t) for t in types]}, receive, send)
        assert sent[0]["status"] == 400 and not calls
        assert limited.upload_slots.locked()
    asyncio.run(check())


@pytest.mark.parametrize("headers", [[], [(b"content-type", b"application/json")],
                                    [(b"content-type", b"multipart/form-data; boundary=dummy")]])
def test_valid_content_type_classification_and_upload_slot_release(headers):
    async def check():
        calls = []
        async def downstream(*args):
            calls.append("parser")
        async def receive():
            pytest.fail("This downstream stub does not read a body")
        limited = BodyLimits(downstream)
        await limited({"type": "http", "headers": headers}, receive, None)
        assert calls == ["parser"] and not limited.upload_slots.locked()
    asyncio.run(check())


def test_duplicate_alternate_columns_rejected_but_distinct_columns_remain_valid():
    definition = deepcopy(references()[0])
    definition["sheets"][0]["fields"]["alt"] = [2, 2]
    with pytest.raises(HTTPException, match="Alternate product columns must be unique") as error:
        validate_definition(definition)
    assert error.value.status_code == 422
    definition["sheets"][0]["fields"]["alt"] = [2, 3]
    validate_definition(definition)


def test_legacy_repeated_names_do_not_repeat_matching_or_change_suggestions(db_session, monkeypatch):
    _, _, _, product = _setup(db_session)
    matcher = NameFirstMatcher(db_session)
    calls = []
    resolve = matcher.resolve_product
    def counted(name, **kwargs):
        calls.append(name)
        return resolve(name, **kwargs)
    monkeypatch.setattr(matcher, "resolve_product", counted)
    assert matcher.reviewed_product(["unmatched crop"] * 64) is None
    assert calls == ["unmatched crop", ""]
    assert matcher.reviewed_product(["unmatched crop"]) is None
    assert len(calls) == 2
    assert matcher.reviewed_product(["Lemongrass"] * 64).id == product.id
    expected = matcher.suggestions(["Lemongras"])
    comparisons = []
    from app.services import name_matcher
    sequence_matcher = name_matcher.difflib.SequenceMatcher
    def score(*args):
        comparisons.append(1)
        return sequence_matcher(*args)
    monkeypatch.setattr(name_matcher.difflib, "SequenceMatcher", score)
    assert matcher.suggestions(["Lemongras", " lemongras "] * 32, code="LEMONGRAS") == expected
    assert len(comparisons) == sum(bool(getattr(product, field)) for field in
                                   ("name_en", "name_vi", "name_th", "t_code", "numeric_code"))


def test_retired_operational_reset_cannot_open_a_database():
    retired = script("reset_operational_data")
    assert "SessionLocal" not in vars(retired)
    with pytest.raises(SystemExit, match="retired; no data was changed"):
        retired.clear_operational_data()


def test_pdf_import_round_trip_with_patched_reader_in_parser_worker(tmp_path):
    import pymupdf

    from app.core.parser_process import parse_document

    path = tmp_path / "dummy-order.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Apples 5 box\nBananas 12 pcs")
        document.save(str(path))
    result = parse_document(str(path), mode="universal", shipment_date="2026-11-12")
    assert [(item["quantity"], item["unit"]) for item in result["drafts"][0]["items"]] == [(5, "box"), (12, "pc")]
