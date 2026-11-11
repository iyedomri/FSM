import uuid

from fastapi import HTTPException
from starlette.responses import JSONResponse

from config import settings


class GuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        limit = settings.max_pdf_bytes if scope["path"] == "/api/ai/generate" else 65536
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get(b"origin", b"").decode("latin-1") != settings.public_origin:
                return await JSONResponse({"detail": "Untrusted request origin"}, 403)(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0 or length > limit:
                raise ValueError()
        except ValueError:
            return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "Request body too large")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"x-request-id", str(uuid.uuid4()).encode()),
                ])
            await send(message)

        await self.app(scope, bounded_receive, secure_send)
