"""Bounded maintenance primitives. No business writes commit inside this module."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import settings
from app.core.file_storage import (
    Content,
    LocalStorage,
    StorageError,
    get_storage,
    lock_object,
    persist_source,
    source_path,
    stage_upload,
    validate_key,
)
from app.core.source_backup import verify_backup
from app.models.order import RawOrderFile


def reconcile_orphans(db, *, store=None, apply=False, offset=0, limit=200,
                      grace=timedelta(days=1), now=None) -> dict:
    if not 1 <= limit <= 500 or offset < 0 or grace < timedelta(days=1):
        raise ValueError("Use a bounded batch and at least a 24-hour grace period")
    if apply and not settings.STORAGE_GC_ENABLED:
        raise ValueError("Object cleanup is disabled; verify references and restore status before enabling it")
    if apply and db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        if db.scalar(text("SHOW transaction_isolation")) != "read committed":
            raise StorageError("Cleanup requires fresh committed reference reads")
    store, now = store or get_storage(), now or datetime.now(UTC)
    entries = store.list(offset=offset, limit=limit)
    result = {"examined": len(entries), "eligible": [], "deleted": [], "referenced": 0, "busy": 0, "recent_or_unmanaged": 0}
    for entry in entries:
        try:
            key = validate_key("intake-v1/" + entry["name"])
            created = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError, StorageError):
            result["recent_or_unmanaged"] += 1
            continue
        if created > now - grace:
            result["recent_or_unmanaged"] += 1
            continue
        uri = store.uri(key)
        if not lock_object(db, uri, wait=False):
            result["busy"] += 1
            continue
        if db.query(RawOrderFile.id).filter_by(storage_url=uri).first():
            result["referenced"] += 1
            continue
        result["eligible"].append(key)
        if apply:
            store.delete(key)
            result["deleted"].append(key)
    # Deletions shift an offset listing. A subsequent batch resumes after the
    # surviving examined entries, not after the old pre-delete offset.
    result["next_offset"] = offset + len(entries) - len(result["deleted"])
    result["possibly_more"] = len(entries) == limit
    return result


def migrate_legacy_source(db, raw_file_id: str, *, store=None, apply=False) -> dict:
    query = db.query(RawOrderFile).filter_by(id=raw_file_id).populate_existing()
    raw = (query.with_for_update() if apply else query).one()
    store = store or get_storage()
    if raw.storage_url.startswith("supabase://"):
        store.key(raw.storage_url)  # Never pretend a different bucket was migrated.
        return {"raw_file_id": raw.id, "status": "already_managed"}
    if raw.storage_url.startswith("local://") and isinstance(store, LocalStorage):
        return {"raw_file_id": raw.id, "status": "already_managed"}
    source_store = LocalStorage(Path(settings.LEGACY_UPLOAD_ROOT)) if raw.storage_url.startswith("local://") else None
    with source_path(raw, store=source_store) as original, original.open("rb") as source, stage_upload(source, raw.file_name) as content:
        result = {"raw_file_id": raw.id, "sha256": content.sha256, "size": content.size,
                  "status": "planned"}
        if apply:
            # Retain the original file. A rollback leaves it usable, and the new
            # object remains staged until reconciliation; never rewrite old bytes.
            raw.storage_url = persist_source(db, content, store=store)
            raw.file_sha256, raw.file_size_bytes = content.sha256, content.size
            db.flush()
            result["status"] = "migrated"
        return result


def export_sources(db, destination: Path, *, after_id="", limit=200, store=None) -> dict:
    """Export one frozen-reference batch. Caller coordinates the DB backup point."""
    if not 1 <= limit <= 1000:
        raise ValueError("Export at most 1000 sources per batch")
    if destination.exists():
        raise ValueError("Choose a new backup batch directory; never overwrite a previous backup")
    destination.mkdir(parents=True)
    rows = db.query(RawOrderFile).filter(RawOrderFile.id > after_id).order_by(RawOrderFile.id).limit(limit + 1).all()
    entries = []
    for raw in rows[:limit]:
        if not raw.file_sha256 or not raw.file_size_bytes or "://" not in raw.storage_url:
            raise StorageError("Migrate legacy sources before exporting a verified backup")
        with source_path(raw, store=store) as path:
            relative = f"objects/{raw.id}{path.suffix}"
            target = destination / relative
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
        entries.append({"raw_file_id": raw.id, "storage_uri": raw.storage_url,
            "file_name": raw.file_name, "sha256": raw.file_sha256, "size": raw.file_size_bytes,
            "backup_path": relative})
    manifest = {"version": 1, "created_at": datetime.now(UTC).isoformat(), "entries": entries,
                "after_id": after_id, "next_after_id": rows[limit-1].id if len(rows) > limit else None}
    # Only a complete, verified batch receives the authoritative manifest.
    verify_backup(destination, manifest)
    temporary = destination / "manifest.pending"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(destination / "manifest.json")
    return {"exported": len(entries), "next_after_id": manifest["next_after_id"]}


def restore_sources(db, directory: Path, *, store=None, apply=False) -> dict:
    """After DB restore, verify each original URI before attaching a new object.

    Writers and GC must be stopped. Re-upload immutable bytes into the isolated
    target, verify the round trip, and change only its source reference/metadata.
    """
    verify_backup(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    prepared = []
    # Preflight every DB mapping before any external write or reference change.
    for entry in sorted(manifest["entries"], key=lambda item: item["raw_file_id"]):
        query = db.query(RawOrderFile).filter_by(id=entry["raw_file_id"]).populate_existing()
        raw = (query.with_for_update() if apply else query).first()
        if (not raw or raw.storage_url != entry["storage_uri"] or raw.file_sha256 != entry["sha256"]
                or raw.file_size_bytes != entry["size"]):
            raise StorageError("Database and source backup points do not match; reconcile before restore")
        prepared.append((raw, entry))
    if apply:
        for raw, entry in prepared:
            path = directory / entry["backup_path"]
            content = Content(path, entry["file_name"], entry["sha256"], entry["size"])
            raw.storage_url = persist_source(db, content, store=store)
            with source_path(raw, store=store):
                pass  # Verify the target download, not just the local backup.
        db.flush()
    return {"sources": len(prepared), "status": "restored" if apply else "planned"}
