import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_order_intake import _setup as intake_setup
from test_request_transaction import request_sessions as request_sessions
from test_system_resilience_audit import audit_client as audit_client

from app.core import database, file_storage
from app.core.config import settings
from app.core.file_storage import (
    LocalStorage,
    StorageError,
    SupabaseStorage,
    get_storage,
    object_key,
    persist_source,
    source_path,
    stage_upload,
)
from app.core.security import create_access_token
from app.main import app
from app.models.order import RawOrderFile
from app.models.request_result import RequestResult
from app.modules.orders.acknowledgment import order_drafts
from app.modules.orders.source_lifecycle import (
    export_sources,
    migrate_legacy_source,
    reconcile_orphans,
    restore_sources,
    verify_backup,
)


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.cell(3, 6, "CT-A")
    for column, value in enumerate(["T001", "Herb", "Herb", None, "100g", 2], 1):
        sheet.cell(5, column, value)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def upload(client, raw, content, key=None, path="matrix"):
    return client.post(f"/api/v3/orders/upload-{path}", headers={"Idempotency-Key": key or str(uuid4())},
        files={"file": ("orders.xlsx", content)}, data={"shipment_date": "2026-09-10", "route_line": "TETE",
            "main_customer_id": raw.main_customer_id})


class Cloud:
    def __init__(self):
        self.objects, self.requests, self.public = {}, [], False
        self.created = {}
        self.fail_put_after_write = False
        self.corrupt_write = False

    def request(self, request):
        path = request.url.path.removeprefix("/storage/v1/")
        self.requests.append(request)
        if path == "bucket/order-sources":
            return httpx.Response(200, json={"id": "order-sources", "public": self.public})
        if request.method == "POST" and path.startswith("object/order-sources/"):
            key = path.removeprefix("object/order-sources/")
            if key in self.objects:
                return httpx.Response(409, json={"message": "exists"})
            self.objects[key] = b"wrong stored bytes" if self.corrupt_write else request.read()
            self.created[key] = datetime.now(UTC).isoformat()
            if self.fail_put_after_write:
                raise httpx.ReadTimeout("provider details must stay private")
            return httpx.Response(200, json={"Key": "order-sources/" + key})
        if request.method == "GET" and path.startswith("object/authenticated/order-sources/"):
            key = path.removeprefix("object/authenticated/order-sources/")
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        if request.method == "POST" and path == "object/list/order-sources":
            import json
            query = json.loads(request.read())
            assert query["prefix"] == "intake-v1" and query["limit"] <= 500
            keys = sorted(self.objects)[query["offset"]:query["offset"]+query["limit"]]
            return httpx.Response(200, json=[{"name": key.removeprefix("intake-v1/"), "created_at": self.created[key]} for key in keys])
        if request.method == "DELETE":
            import json
            for key in json.loads(request.read())["prefixes"]:
                self.objects.pop(key, None)
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    def adapter(self, key="sb_secret_disposable_test_only"):
        return SupabaseStorage("https://test.supabase.co", key, "order-sources",
                               transport=httpx.MockTransport(self.request))


def test_uploaded_source_and_replay_are_atomic_and_original_is_authorized(audit_client, db_session):
    client, _ = audit_client
    original, _ = intake_setup(db_session)
    content, key = workbook_bytes(), str(uuid4())
    first = upload(client, original, content, key)
    assert first.status_code == 200, first.text
    replay = upload(client, original, content, key)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert db_session.query(RawOrderFile).count() == 2
    assert db_session.query(RequestResult).count() == 1
    raw = db_session.get(RawOrderFile, first.json()["raw_file_id"])
    assert raw.storage_url.startswith("local://intake-v1/")
    assert raw.file_name == "orders.xlsx"
    assert raw.file_sha256 == hashlib.sha256(content).hexdigest() and raw.file_size_bytes == len(content)
    result = client.get(f"/api/v3/orders/{raw.id}/source")
    assert result.status_code == 200 and result.content == content
    assert result.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v3/orders/upload-results/{key}?channel=matrix").json() == first.json()
    assert client.get(f"/api/v3/orders/upload-results/{uuid4()}?channel=matrix").status_code == 404
    assert TestClient(app).get(f"/api/v3/orders/{raw.id}/source").status_code == 401
    raw.acknowledgment_summary = {}  # Force the fallback path to read immutable bytes.
    assert order_drafts(raw)[0].client_code == "CT-A"


def test_changed_upload_bytes_or_context_cannot_reuse_key(audit_client, db_session):
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    content, key = workbook_bytes(), str(uuid4())
    assert upload(client, raw, content, key).status_code == 200
    assert upload(client, raw, content + b"different", key).status_code == 409
    # Another explicit submission of the same bytes is allowed.
    assert upload(client, raw, content).status_code == 200
    assert db_session.query(RawOrderFile).count() == 3


def test_universal_upload_preserves_original_filename_and_content(audit_client, db_session):
    client, _ = audit_client
    original, _ = intake_setup(db_session)
    content = workbook_bytes()
    result = upload(client, original, content, path="universal")
    assert result.status_code == 200, result.text
    raw = db_session.get(RawOrderFile, result.json()["raw_file_id"])
    assert raw.file_name == "orders.xlsx" and raw.file_size_bytes == len(content)
    assert client.get(f"/api/v3/orders/{raw.id}/source").content == content


def test_private_cloud_reads_work_from_fresh_host_without_local_original(audit_client, db_session, monkeypatch):
    client, _ = audit_client
    original, _ = intake_setup(db_session)
    cloud = Cloud()
    monkeypatch.setattr(file_storage, "get_storage", cloud.adapter)
    content = workbook_bytes()
    result = upload(client, original, content)
    assert result.status_code == 200, result.text
    raw = db_session.get(RawOrderFile, result.json()["raw_file_id"])
    assert raw.storage_url.startswith("supabase://order-sources/intake-v1/")
    raw.acknowledgment_summary = {}
    # Each get_storage call constructs a fresh HTTP client, sharing only the
    # simulated remote service. No durable source lives on this host.
    assert order_drafts(raw)[0].client_code == "CT-A"
    assert client.get(f"/api/v3/orders/{raw.id}/source").content == content
    assert all("public/" not in request.url.path for request in cloud.requests)
    assert all(request.headers["apikey"].startswith("sb_secret_") and "authorization" not in request.headers for request in cloud.requests)


@pytest.mark.parametrize("secret", ["sb_secret_disposable_test_only", "legacy-service-role-jwt"])
def test_storage_uses_documented_secret_headers_and_never_overwrites(tmp_path, secret):
    cloud = Cloud()
    adapter = cloud.adapter(secret)
    with stage_upload(BytesIO(b"file"), "source.xlsx") as content:
        key = object_key(content)
        adapter.put(key, content)
        with pytest.raises(StorageError):
            adapter.put(key, content)
        target = tmp_path / "download.xlsx"
        adapter.download(key, target)
        assert target.read_bytes() == b"file"
    post = next(request for request in cloud.requests if request.method == "POST")
    assert post.headers["x-upsert"] == "false" and post.headers["content-length"] == "4"
    assert ("authorization" in post.headers) == (not secret.startswith("sb_secret_"))


def test_public_bucket_and_cross_bucket_keys_fail_closed():
    cloud = Cloud()
    cloud.public = True
    with pytest.raises(StorageError, match="private"):
        cloud.adapter().check_private()
    with pytest.raises(StorageError):
        cloud.adapter().key("supabase://another-bucket/intake-v1/key.xlsx")
    with pytest.raises(StorageError):
        SupabaseStorage("https://attacker.invalid", "sb_secret_secret", "order-sources")


def test_readiness_rejects_a_public_document_bucket(audit_client, monkeypatch):
    import importlib
    client, _ = audit_client
    cloud = Cloud()
    cloud.public = True
    monkeypatch.setattr(settings, "FILE_STORAGE_PROVIDER", "supabase")
    monkeypatch.setattr(importlib.import_module("app.main"), "get_storage", cloud.adapter)
    assert client.get("/ready").status_code == 503


def test_backup_can_be_verified_offline_without_database_configuration(tmp_path):
    import json
    import os
    import subprocess
    import sys
    path = tmp_path / "source.xlsx"
    path.write_bytes(b"fixture")
    manifest = {"version": 1, "entries": [{"raw_file_id": "one", "backup_path": "source.xlsx",
        "sha256": hashlib.sha256(b"fixture").hexdigest(), "size": 7}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = os.environ.copy()
    for key in ["DATABASE_URL", "SUPABASE_DATABASE_URL", "BOX_SOURCE_DATABASE_URL"]:
        env.pop(key, None)
    script = Path(__file__).resolve().parents[1] / "scripts/manage_order_sources.py"
    result = subprocess.run([sys.executable, str(script), "verify", "--directory", str(tmp_path)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"verified": 1}


def test_upload_timeout_after_remote_write_never_creates_false_database_success(audit_client, db_session, monkeypatch):
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    cloud = Cloud()
    cloud.fail_put_after_write = True
    monkeypatch.setattr(file_storage, "get_storage", cloud.adapter)
    result = upload(client, raw, workbook_bytes())
    assert result.status_code == 503
    assert "provider details" not in result.text
    assert db_session.query(RawOrderFile).count() == 1
    assert db_session.query(RequestResult).count() == 0
    assert len(cloud.objects) == 1  # Staged orphan: never a broken committed reference.
    monkeypatch.setattr(settings, "STORAGE_GC_ENABLED", True)
    cleanup = reconcile_orphans(db_session, store=cloud.adapter(), apply=True, now=datetime.now(UTC)+timedelta(days=2))
    assert len(cleanup["deleted"]) == 1 and not cloud.objects


def test_successful_put_with_wrong_stored_bytes_does_not_publish_a_reference(audit_client, db_session, monkeypatch):
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    cloud = Cloud()
    cloud.corrupt_write = True
    monkeypatch.setattr(file_storage, "get_storage", cloud.adapter)
    result = upload(client, raw, workbook_bytes())
    assert result.status_code == 503
    assert db_session.query(RawOrderFile).count() == 1
    assert db_session.query(RequestResult).count() == 0


@pytest.mark.parametrize("damage", ["bytes", "metadata", "missing"])
def test_content_tampering_and_missing_integrity_metadata_are_detected(audit_client, db_session, damage):
    client, _ = audit_client
    original, _ = intake_setup(db_session)
    result = upload(client, original, workbook_bytes())
    raw = db_session.get(RawOrderFile, result.json()["raw_file_id"])
    store = get_storage()
    path = store._path(store.key(raw.storage_url))
    if damage == "bytes":
        path.write_bytes(b"tampered")
    elif damage == "missing":
        path.unlink()
    else:
        raw.file_sha256 = raw.file_size_bytes = None
        db_session.commit()
    assert client.get(f"/api/v3/orders/{raw.id}/source").status_code == 503


def test_legacy_paths_outside_approved_root_are_not_read(db_session, tmp_path, monkeypatch):
    raw, _ = intake_setup(db_session)
    outside = tmp_path / "private.xlsx"
    outside.write_bytes(b"must not escape")
    monkeypatch.setattr(settings, "LEGACY_UPLOAD_ROOT", str(tmp_path / "approved"))
    raw.storage_url = str(outside)
    with pytest.raises(StorageError, match="approved upload root"):
        with source_path(raw):
            pytest.fail("Out-of-root source must not be yielded")


def test_upload_and_download_byte_limits_are_enforced_before_parser_work(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "MAX_UPLOAD_BYTES", 8)
    with pytest.raises(file_storage.UploadTooLarge):
        with stage_upload(BytesIO(b"123456789"), "orders.xlsx"):
            pytest.fail("Oversized source must not reach the parser")
    cloud = Cloud()
    with stage_upload(BytesIO(b"1234"), "orders.xlsx") as content:
        key = object_key(content)
    cloud.objects[key] = b"123456789"
    with pytest.raises(StorageError, match="size limit"):
        cloud.adapter().download(key, tmp_path / "too-large.xlsx")


def test_parser_failure_releases_temporary_file_and_has_no_durable_effect(audit_client, db_session, monkeypatch):
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    temporary = []
    original = file_storage.TemporaryDirectory
    def tracked(*args, **kwargs):
        directory = original(*args, **kwargs)
        temporary.append(Path(directory.name))
        return directory
    monkeypatch.setattr(file_storage, "TemporaryDirectory", tracked)
    assert upload(client, raw, b"invalid workbook").status_code == 422
    assert temporary and not any(path.exists() for path in temporary)
    assert db_session.query(RawOrderFile).count() == 1
    assert not list(Path(settings.LOCAL_STORAGE_ROOT).rglob("*.xlsx"))


@pytest.mark.parametrize("filename", ["invalid.pdf", "invalid.xls"])
def test_malformed_supported_documents_have_safe_parse_errors(audit_client, db_session, filename):
    client, _ = audit_client
    raw, _ = intake_setup(db_session)
    result = client.post("/api/v3/orders/upload-universal", headers={"Idempotency-Key": str(uuid4())},
        files={"file": (filename, b"not a valid document")}, data={"shipment_date": "2026-09-10", "main_customer_id": raw.main_customer_id})
    assert result.status_code == 422, result.text
    assert db_session.query(RawOrderFile).count() == 1


def test_original_basename_is_preserved_for_parser_customer_detection():
    with stage_upload(BytesIO(workbook_bytes()), "Hungfat order.xlsx") as content:
        from app.services.unified_excel_parser import UnifiedExcelParser
        assert UnifiedExcelParser(str(content.path)).file_name == "Hungfat order.xlsx"


def test_legacy_migration_backup_and_restore_recover_source_after_storage_loss(db_session, tmp_path, monkeypatch):
    raw, _ = intake_setup(db_session)
    original = tmp_path / "legacy.xlsx"
    original.write_bytes(workbook_bytes())
    raw.storage_url = str(original)
    db_session.commit()
    assert migrate_legacy_source(db_session, raw.id)["status"] == "planned"
    assert raw.storage_url == str(original)
    assert migrate_legacy_source(db_session, raw.id, apply=True)["status"] == "migrated"
    db_session.commit()
    original_uri = raw.storage_url
    assert original.exists()  # Migration does not discard the recovery source.
    backup = tmp_path / "backup"
    assert export_sources(db_session, backup)["exported"] == 1
    assert verify_backup(backup) == {"verified": 1}
    db_session.commit()
    # Restore the SQLite database snapshot into an independent database engine,
    # then lose the original object store. Supabase's actual restore is a gate.
    restored_database = create_engine("sqlite://")
    source_connection = db_session.get_bind().raw_connection()
    target_connection = restored_database.raw_connection()
    source_connection.driver_connection.backup(target_connection.driver_connection)
    source_connection.close()
    target_connection.close()
    get_storage().delete(get_storage().key(raw.storage_url))
    with pytest.raises(StorageError):
        with source_path(raw):
            pass
    restored_host = LocalStorage(tmp_path / "restored-host")
    try:
        with Session(restored_database) as recovered:
            assert restore_sources(recovered, backup, store=restored_host, apply=True)["status"] == "restored"
            recovered.commit()
            restored_raw = recovered.get(RawOrderFile, raw.id)
            assert restored_raw.storage_url != original_uri
            with source_path(restored_raw, store=restored_host) as path:
                assert path.read_bytes() == original.read_bytes()
            monkeypatch.setattr(file_storage, "get_storage", lambda: restored_host)
            from app.models.user import User
            from app.modules.orders.validation import OrderValidationService
            validation = OrderValidationService(recovered).validate(raw_file_id=raw.id,
                actor=recovered.get(User, raw.uploaded_by))
            assert validation.total_product_rows == validation.resolved_product_rows == 1
    finally:
        restored_database.dispose()


def test_backup_rejects_tampering_and_database_point_mismatch(db_session, tmp_path):
    raw, _ = intake_setup(db_session)
    path = tmp_path / "legacy.xlsx"
    path.write_bytes(workbook_bytes())
    raw.storage_url = str(path)
    migrate_legacy_source(db_session, raw.id, apply=True)
    db_session.commit()
    backup = tmp_path / "backup"
    export_sources(db_session, backup)
    raw.storage_url = "local://different-reference"
    with pytest.raises(StorageError, match="points do not match"):
        restore_sources(db_session, backup, apply=True)
    source = next((backup / "objects").iterdir())
    source.write_bytes(b"damaged backup")
    with pytest.raises(StorageError, match="integrity"):
        verify_backup(backup)


def test_cleanup_preserves_referenced_recent_and_busy_objects_and_removes_rollback_orphan(db_session, tmp_path, monkeypatch):
    raw, _ = intake_setup(db_session)
    store = get_storage()
    with stage_upload(BytesIO(workbook_bytes()), "orders.xlsx") as content:
        raw.storage_url = persist_source(db_session, content)
        raw.file_sha256, raw.file_size_bytes = content.sha256, content.size
        db_session.commit()
        referenced = store.key(raw.storage_url)
        orphan = store.key(persist_source(db_session, content))
        db_session.rollback()
        recent = store.key(persist_source(db_session, content))
        busy = store.key(persist_source(db_session, content))
        db_session.rollback()
    now = datetime.now(UTC)
    import os
    for key in [referenced, orphan, busy]:
        stamp = (now - timedelta(days=2)).timestamp()
        os.utime(store._path(key), (stamp, stamp))
    from app.modules.orders import source_lifecycle
    monkeypatch.setattr(source_lifecycle, "lock_object", lambda db, uri, wait: not uri.endswith(busy))
    plan = reconcile_orphans(db_session, now=now)
    assert plan["eligible"] == [orphan]
    with pytest.raises(ValueError, match="disabled"):
        reconcile_orphans(db_session, apply=True)
    monkeypatch.setattr(settings, "STORAGE_GC_ENABLED", True)
    result = reconcile_orphans(db_session, now=now, apply=True)
    assert result["deleted"] == [orphan] and result["referenced"] == result["busy"] == 1
    assert not store._path(orphan).exists()
    assert all(store._path(key).exists() for key in [referenced, recent, busy])


@pytest.mark.parametrize("commit_fails", [False, True])
def test_upload_commit_failure_and_lost_response_do_not_delete_committed_source(request_sessions, tmp_path, monkeypatch, commit_fails):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    with request_sessions() as seed:
        raw, _ = intake_setup(seed)
        main_id, actor_id = raw.main_customer_id, raw.uploaded_by
    headers = {"Authorization": "Bearer " + create_access_token({"sub": actor_id}), "Idempotency-Key": str(uuid4())}
    content = workbook_bytes()
    def send(client):
        return client.post("/api/v3/orders/upload-matrix", headers=headers,
            files={"file": ("orders.xlsx", content)}, data={"shipment_date": "2026-09-10", "route_line": "TETE", "main_customer_id": main_id})
    if commit_fails:
        def failing_session():
            db = request_sessions()
            def fail():
                raise RuntimeError("Simulated commit failure")
            monkeypatch.setattr(db, "commit", fail)
            return db
        with monkeypatch.context() as patch:
            patch.setattr(database, "SessionLocal", failing_session)
            assert send(TestClient(app, raise_server_exceptions=False)).status_code == 500
    else:
        async def disconnected(scope, receive, send):
            async def fail(message):
                if message["type"] == "http.response.start":
                    raise ConnectionError("Response lost after commit")
                await send(message)
            await app(scope, receive, fail)
        with pytest.raises(ConnectionError):
            send(TestClient(disconnected))
    with request_sessions() as observer:
        assert observer.query(RawOrderFile).count() == (1 if commit_fails else 2)
    # Retrying an uncertain committed response uses identical bytes, not a freshly
    # generated ZIP with a different embedded timestamp.
    assert list(Path(settings.LOCAL_STORAGE_ROOT).rglob("*.xlsx"))
    retry = send(TestClient(app))
    assert retry.status_code == 200, retry.text
    with request_sessions() as observer:
        assert observer.query(RawOrderFile).count() == 2
        assert observer.query(RequestResult).count() == 1
        source = observer.get(RawOrderFile, retry.json()["raw_file_id"])
        with source_path(source) as path:
            assert path.read_bytes() == content
