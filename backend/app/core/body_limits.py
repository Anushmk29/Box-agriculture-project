"""Bound actual ASGI bytes (including chunked requests), not just Content-Length."""

import asyncio
from time import monotonic

from starlette.responses import JSONResponse

from app.core.work_limits import WorkLimit

JSON_BYTES = 2 * 1024 * 1024
MULTIPART_BYTES = 26 * 1024 * 1024


class BodyLimits:
    def __init__(self, app):
        self.app = app
        self.upload_slots = asyncio.Semaphore(2)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and sum(name == b"content-type" for name, _ in scope.get("headers", [])) > 1:
            return await JSONResponse(status_code=400, content={"detail": "Send exactly one Content-Type header"})(scope, receive, send)
        multipart = scope["type"] == "http" and dict(scope.get("headers", [])).get(b"content-type", b"").lower().startswith(b"multipart/")
        if multipart:
            # One ASGI event loop per worker; no await between locked() and the
            # immediately available acquire. Never queue/spool unlimited uploads.
            if self.upload_slots.locked():
                return await JSONResponse(status_code=503, content={"detail": "Upload capacity is busy. Retry the original submission."},
                    headers={"Retry-After": "2"})(scope, receive, send)
            await self.upload_slots.acquire()
        try:
            return await self._limited(scope, receive, send)
        finally:
            if multipart:
                self.upload_slots.release()

    async def _limited(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        limit = MULTIPART_BYTES if headers.get(b"content-type", b"").lower().startswith(b"multipart/") else JSON_BYTES
        length = headers.get(b"content-length")
        if length and (len(length) > 10 or not length.isdigit() or int(length) > limit):
            return await JSONResponse(status_code=413, content={"detail": "Request body exceeds its size budget"})(scope, receive, send)
        received, started = 0, None

        async def bounded_receive():
            nonlocal received, started
            started = started or monotonic()
            remaining = 30 - (monotonic() - started)
            if remaining <= 0:
                raise WorkLimit("Request body read budget expired; retry the original submission", busy=True)
            try:
                message = await asyncio.wait_for(receive(), timeout=min(10, remaining))
            except TimeoutError:
                raise WorkLimit("Request body read budget expired; retry the original submission", busy=True) from None
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise WorkLimit("Request body exceeds its size budget")
            return message
        return await self.app(scope, bounded_receive, send)
