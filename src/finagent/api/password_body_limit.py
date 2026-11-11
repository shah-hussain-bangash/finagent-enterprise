"""Count password-login bytes even when Content-Length is absent or inaccurate."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finagent.core.correlation import get_correlation_id
from finagent.core.errors import ErrorCode, error_body


class PasswordBodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        passkey = scope.get("path", "").startswith("/api/v1/auth/passkeys/")
        if scope["type"] != "http" or (
            not passkey
            and scope.get("path")
            not in (
                "/api/v1/auth/password/login",
                "/api/v1/auth/totp/verify",
                "/api/v1/auth/email/verify",
            )
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > (32768 if passkey else 4096):
                response = JSONResponse(
                    status_code=413,
                    content=error_body(
                        ErrorCode.PAYLOAD_TOO_LARGE, "Request body is too large.", get_correlation_id()
                    ),
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
