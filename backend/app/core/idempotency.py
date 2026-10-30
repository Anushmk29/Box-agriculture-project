"""Reserve and complete a write receipt in the caller's business transaction.

The unique insert waits for a competing transaction, then reads its committed
receipt. A rollback removes both the business write and its reservation. There
is no commit here, no automatic replay of business code and no expiring keys.
"""

import hashlib
import json
import re
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.request_result import RequestResult


def validate_request_key(key: str | None) -> str:
    if key is None:
        raise HTTPException(428, detail={"code": "REQUEST_KEY_REQUIRED",
            "message": "Send an Idempotency-Key for this submission; reuse it when retrying."})
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key, flags=re.ASCII):
        raise HTTPException(422, detail="Idempotency-Key must contain 16–128 letters, digits, underscores or hyphens")
    return key


def run_once(db: Session, *, actor_id: str, operation: str, key: str | None,
             payload: dict, execute: Callable[[], dict]) -> dict:
    key = validate_request_key(key)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    identity = dict(actor_id=actor_id, operation=operation, request_key=key)
    dialect = db.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("Request receipts require the configured Supabase database")
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    # Supabase transaction/session pooling both support transaction-local settings.
    # Bound unique-key waits without changing the remainder of the request's policy.
    previous_timeout = None
    if dialect == "postgresql":
        previous_timeout = int(db.scalar(text("SELECT setting FROM pg_settings WHERE name='lock_timeout'")))
        timeout = min(previous_timeout, 5000) if previous_timeout else 5000
        db.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": f"{timeout}ms"})
    try:
        claimed = db.execute(insert(RequestResult).values(**identity, request_sha256=digest)
            .on_conflict_do_nothing(index_elements=["actor_id", "operation", "request_key"])
            .returning(RequestResult.request_key)).scalar_one_or_none()
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) == "55P03":
            raise HTTPException(409, detail={"code": "REQUEST_IN_PROGRESS",
                "message": "This submission is still processing. Retry with the same key and details."},
                headers={"Retry-After": "1"}) from exc
        raise
    if previous_timeout is not None:
        db.execute(text("SELECT set_config('lock_timeout', :value, true)"), {"value": f"{previous_timeout}ms"})
    # Completed receipts are immutable. Readers need no row lock after the
    # unique insert has resolved the winner; only the claimant writes its row.
    receipt = db.execute(select(RequestResult).filter_by(**identity)
        .execution_options(populate_existing=True)).scalar_one()
    if receipt.request_sha256 != digest:
        raise HTTPException(409, detail={"code": "REQUEST_KEY_REUSED",
            "message": "This submission key belongs to different details. Retry the original submission."})
    if claimed is None:
        if receipt.response_json is None:
            # A malformed/imported receipt is not permission to repeat a write.
            raise HTTPException(409, detail={"code": "REQUEST_RESULT_UNAVAILABLE",
                "message": "This submission needs review; its saved result is unavailable."})
        return receipt.response_json
    result = execute()
    # Detach/canonicalize the small JSON response before the transaction commits.
    receipt.response_json = json.loads(json.dumps(result, allow_nan=False))
    db.flush()
    return receipt.response_json
