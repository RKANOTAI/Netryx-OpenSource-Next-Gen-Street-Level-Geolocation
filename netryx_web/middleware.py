"""Bound request bodies before Starlette's multipart parser allocates files."""
from tempfile import SpooledTemporaryFile

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes=42 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        limit = self.max_bytes if scope["path"] == "/api/v1/photo-geolocations" else 65536
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length < 0 or length > limit:
            return await self._reject(scope, receive, send)
        # Spool above 1 MiB; never trust Content-Length for chunked uploads.
        with SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    return await self._reject(scope, receive, send)
                await run_in_threadpool(body.write, chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            remaining = total
            finished = False

            async def replay():
                nonlocal remaining, finished
                if finished:
                    return await receive()
                chunk = await run_in_threadpool(body.read, 65536)
                remaining -= len(chunk)
                finished = remaining == 0
                return {"type": "http.request", "body": chunk, "more_body": not finished}

            await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope, receive, send):
        response = JSONResponse(status_code=413, content={"detail": {
            "code": "REQUEST_TOO_LARGE",
            "message_fr": "L’envoi dépasse la taille maximale autorisée.",
        }})
        await response(scope, receive, send)
