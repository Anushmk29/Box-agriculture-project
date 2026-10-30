"""Private immutable intake objects; local disk is a development adapter only.

An object is staged until its RawOrderFile reference commits. Never delete on an
uncertain commit. The reconciler shares the writer's transaction-scoped lock.
"""

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import BinaryIO
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.storage_errors import StorageError as StorageError
from app.core.storage_errors import UploadTooLarge as UploadTooLarge

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUFFIXES = {".xlsx", ".xlsm", ".xls", ".pdf"}
MIME = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel", ".pdf": "application/pdf"}
KEY_PATTERN = re.compile(r"intake-v1/(\d{8}T\d{6})_[a-f0-9]{32}_[a-f0-9]{64}\.(xlsx|xlsm|xls|pdf)")


@dataclass(frozen=True)
class Content:
    path: Path
    filename: str
    sha256: str
    size: int


def safe_filename(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    if (not name or len(name) > 256 or any(ord(char) < 32 or char in '<>:"|?*' for char in name)
            or name.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError("Enter a valid file name of at most 256 characters")
    if Path(name).suffix.lower() not in SUFFIXES:
        raise ValueError("Unsupported order file format")
    return name


def _copy(source: BinaryIO, destination: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with destination.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise UploadTooLarge("File exceeds the 25 MB upload limit")
            digest.update(chunk)
            target.write(chunk)
    if not size:
        raise ValueError("The uploaded file is empty")
    return digest.hexdigest(), size


@contextmanager
def stage_upload(source: BinaryIO, filename: str) -> Iterator[Content]:
    name = safe_filename(filename)
    with TemporaryDirectory(prefix="box-intake-") as scratch:
        # Parser format/customer detection also uses the original basename.
        path = Path(scratch) / name
        digest, size = _copy(source, path)
        yield Content(path, name, digest, size)


def object_key(content: Content, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    return f"intake-v1/{stamp}_{uuid4().hex}_{content.sha256}{content.path.suffix.lower()}"


def validate_key(key: str) -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise StorageError("Invalid managed document key")
    return key


def lock_object(db, uri: str, *, wait: bool = True) -> bool:
    """Held through the caller's COMMIT/ROLLBACK, including uncertain commits."""
    if db.get_bind().dialect.name == "sqlite":
        return True  # Unit adapter only; no SQLite claim about interprocess locks.
    if db.get_bind().dialect.name != "postgresql":
        raise StorageError("Unsupported document locking backend")
    identity = int.from_bytes(hashlib.sha256(uri.encode()).digest()[:8], "big", signed=True)
    function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
    result = db.scalar(text(f"SELECT {function}(:key)"), {"key": identity})
    return True if wait else bool(result)


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def uri(self, key: str) -> str:
        return "local://" + validate_key(key)

    def key(self, uri: str) -> str:
        if not uri.startswith("local://"):
            raise StorageError("Unexpected document provider")
        return validate_key(uri.removeprefix("local://"))

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("Document path escapes the storage root")
        return path

    def check_private(self):
        if settings.ENVIRONMENT.lower() not in {"development", "test"}:
            raise StorageError("Local storage is development-only")

    def put(self, key: str, content: Content):
        self.check_private()
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # No overwrite even when a caller accidentally reuses a key.
        with path.open("xb") as target, content.path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)

    def download(self, key: str, target: Path):
        try:
            with self._path(key).open("rb") as source:
                _copy(source, target)
        except (OSError, ValueError) as exc:
            raise StorageError("Document could not be read") from exc

    def delete(self, key: str):
        self._path(key).unlink(missing_ok=True)

    def list(self, *, offset: int, limit: int) -> list[dict]:
        directory = self.root / "intake-v1"
        if not directory.exists():
            return []
        # Development only; hosted adapter delegates bounded listing to Storage.
        paths = sorted(directory.iterdir(), key=lambda item: item.name)[offset:offset+limit]
        return [{"name": path.name, "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()}
                for path in paths if path.is_file()]


class SupabaseStorage:
    def __init__(self, url: str, key: str, bucket: str, *, transport=None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.supabase\.co", parsed.netloc)
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise StorageError("Configure the project's HTTPS Supabase API URL")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", bucket):
            raise StorageError("Invalid private document bucket")
        if not key or key.startswith("sb_publishable_"):
            raise StorageError("A server-side Storage secret is required")
        self.url, self.bucket, self.secret, self.transport = url.rstrip("/"), bucket, key, transport

    def _client(self):
        headers = {"apikey": self.secret}
        # New opaque secret keys belong only in apikey. Legacy service-role JWTs
        # also need Authorization; never attach an application user's custom JWT.
        if not self.secret.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.secret}"
        return httpx.Client(base_url=self.url + "/storage/v1/", headers=headers,
            timeout=httpx.Timeout(5), follow_redirects=False,
            transport=self.transport, trust_env=False)

    def uri(self, key: str) -> str:
        return f"supabase://{self.bucket}/{validate_key(key)}"

    def key(self, uri: str) -> str:
        parsed = urlsplit(uri)
        if parsed.scheme != "supabase" or parsed.netloc != self.bucket or parsed.query or parsed.fragment:
            raise StorageError("Unexpected document bucket or provider")
        return validate_key(parsed.path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs):
        deadline = monotonic() + 15
        content = kwargs.get("content")
        if content is not None and hasattr(content, "read"):
            def chunks():
                while chunk := content.read(1024 * 1024):
                    if monotonic() > deadline:
                        raise StorageError("Document storage work budget expired")
                    yield chunk
            kwargs["content"] = chunks()
        try:
            with self._client() as client, client.stream(method, path, **kwargs) as response:
                if not response.is_success:
                    raise StorageError("Private document storage is unavailable")
                data = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() > deadline or len(data) + len(chunk) > 1024 * 1024:
                        raise StorageError("Document storage response exceeded its work budget")
                    data.extend(chunk)
                return httpx.Response(response.status_code, content=bytes(data),
                    headers={"Content-Type": response.headers.get("Content-Type", "application/json")})
        except httpx.HTTPError:
            raise StorageError("Private document storage is unavailable") from None

    def check_private(self):
        try:
            data = self._request("GET", f"bucket/{self.bucket}").json()
            if data.get("id") != self.bucket or data.get("public") is not False:
                raise StorageError("The document bucket must be private")
        except (ValueError, AttributeError):
            raise StorageError("Invalid document bucket response") from None

    def put(self, key: str, content: Content):
        self.check_private()
        with content.path.open("rb") as source:
            self._request("POST", f"object/{self.bucket}/{quote(validate_key(key), safe='/')}",
                content=source, headers={"Content-Type": MIME[content.path.suffix.lower()],
                    "Content-Length": str(content.size), "x-upsert": "false", "Cache-Control": "no-store"})

    def download(self, key: str, target: Path):
        self.check_private()
        deadline = monotonic() + 15
        try:
            with self._client() as client, client.stream("GET",
                    f"object/authenticated/{self.bucket}/{quote(validate_key(key), safe='/')}") as response:
                if not response.is_success:
                    raise StorageError("Private document could not be read")
                size = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes():
                        if monotonic() > deadline:
                            raise StorageError("Document download work budget expired")
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise StorageError("Stored document exceeds the size limit")
                        output.write(chunk)
        except httpx.HTTPError:
            raise StorageError("Private document could not be read") from None

    def delete(self, key: str):
        self.check_private()
        self._request("DELETE", f"object/{self.bucket}", json={"prefixes": [validate_key(key)]})

    def list(self, *, offset: int, limit: int) -> list[dict]:
        self.check_private()
        try:
            data = self._request("POST", f"object/list/{self.bucket}", json={"prefix": "intake-v1",
                "limit": limit, "offset": offset, "sortBy": {"column": "name", "order": "asc"}}).json()
            if not isinstance(data, list) or len(data) > limit:
                raise StorageError("Invalid document inventory response")
            return data
        except ValueError:
            raise StorageError("Invalid document inventory response") from None


def get_storage():
    if settings.FILE_STORAGE_PROVIDER == "supabase":
        return SupabaseStorage(settings.SUPABASE_URL, settings.SUPABASE_STORAGE_KEY.get_secret_value(),
                               settings.SUPABASE_STORAGE_BUCKET)
    if settings.FILE_STORAGE_PROVIDER == "local" and settings.ENVIRONMENT.lower() in {"development", "test"}:
        return LocalStorage(Path(settings.LOCAL_STORAGE_ROOT))
    raise StorageError("Private document storage must be configured")


def persist_source(db, content: Content, *, store=None) -> str:
    store = store or get_storage()
    key = object_key(content)
    uri = store.uri(key)
    lock_object(db, uri)
    store.put(key, content)
    # Verify actual stored bytes before a business transaction can publish its
    # reference. A successful PUT alone is not an end-to-end integrity check.
    with TemporaryDirectory(prefix="box-verify-") as scratch:
        received = Path(scratch) / ("source" + content.path.suffix.lower())
        store.download(key, received)
        with received.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != content.sha256 or received.stat().st_size != content.size:
            raise StorageError("Stored document failed its upload integrity check")
    # Caller sets RawOrderFile metadata and commits it with the business work.
    # Failure after this point leaves a staged orphan for safe reconciliation.
    return uri


@contextmanager
def source_path(raw_file, *, store=None) -> Iterator[Path]:
    uri = raw_file.storage_url
    if "://" not in uri:
        root = Path(settings.LEGACY_UPLOAD_ROOT).resolve()
        path = Path(uri).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise StorageError("Legacy source is unavailable in the approved upload root")
        # Legacy files have no checksum yet. Migration verifies and records one.
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise StorageError("Legacy document exceeds the size limit")
        yield path
        return
    store = store or get_storage()
    key = store.key(uri)
    if not raw_file.file_sha256 or not raw_file.file_size_bytes:
        raise StorageError("Stored document integrity metadata is missing")
    with TemporaryDirectory(prefix="box-source-") as scratch:
        path = Path(scratch) / safe_filename(raw_file.file_name)
        store.download(key, path)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != raw_file.file_sha256 or path.stat().st_size != raw_file.file_size_bytes:
            raise StorageError("Stored document failed its integrity check")
        yield path
