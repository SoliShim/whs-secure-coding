"""Pure ASGI request-body limit applied before form or multipart parsing."""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_HTTP_BODY_BYTES = 6 * 1024 * 1024


class _RequestBodyTooLarge(BaseException):
    """Bypass form-parser Exception catches and return control to the outer middleware."""


def _is_body_limit_signal(exc: BaseException) -> bool:
    if isinstance(exc, _RequestBodyTooLarge):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_body_limit_signal(item) for item in exc.exceptions)
    return False


class RequestBodyLimitMiddleware:
    """Reject declared or streamed request bodies over a fixed byte ceiling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_HTTP_BODY_BYTES,
        response_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.response_headers = dict(response_headers or {})

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        error: str,
    ) -> None:
        response = JSONResponse({"error": error}, status_code=status_code)
        for name, value in self.response_headers.items():
            response.headers[name] = value
        response.headers["X-Request-ID"] = secrets.token_hex(12)
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        lengths = [value for name, value in scope.get("headers", []) if name == b"content-length"]
        if lengths:
            try:
                if len(lengths) != 1:
                    raise ValueError
                declared = int(lengths[0].decode("ascii"), 10)
            except (UnicodeDecodeError, ValueError):
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=400,
                    error="invalid_content_length",
                )
                return
            if declared < 0:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=400,
                    error="invalid_content_length",
                )
                return
            if declared > self.max_body_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    status_code=413,
                    error="request_too_large",
                )
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    scope.setdefault("state", {})["request_body_limit_exceeded"] = True
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except BaseException as exc:
            if not _is_body_limit_signal(exc) or response_started:
                raise
            await self._reject(
                scope,
                receive,
                send,
                status_code=413,
                error="request_too_large",
            )
