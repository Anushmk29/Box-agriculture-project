"""Offline backup verification: no app settings, credentials or database imports."""

import hashlib
import json
from pathlib import Path

from app.core.storage_errors import StorageError


def verify_backup(directory: Path, manifest: dict | None = None) -> dict:
    root = directory.resolve()
    if manifest is None:
        manifest_path = root / "manifest.json"
        if manifest_path.stat().st_size > 2 * 1024 * 1024:
            raise StorageError("Backup manifest exceeds the size limit")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("entries"), list) or len(manifest["entries"]) > 1000:
        raise ValueError("Unsupported or oversized source backup manifest")
    identities = set()
    for entry in manifest["entries"]:
        path = (root / entry["backup_path"]).resolve()
        if (not path.is_relative_to(root) or not path.is_file() or entry["raw_file_id"] in identities
                or not 0 < entry["size"] <= 26214400 or path.stat().st_size > 26214400):
            raise StorageError("Backup contains an invalid path or duplicate source identity")
        identities.add(entry["raw_file_id"])
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != entry["sha256"] or path.stat().st_size != entry["size"]:
            raise StorageError("Backup source failed its integrity check")
    return {"verified": len(identities)}

