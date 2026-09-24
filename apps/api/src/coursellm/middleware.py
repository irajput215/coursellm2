"""ASGI middleware: request correlation, access logging and security headers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Any

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from coursellm.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"

# Headers applied to every response. Kept minimal because TLS/HSTS are
# terminated at the load balancer in the deployed topology; these are the
# application-level protections that must hold even behind a proxy.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _client_request_id(scope: Scope) -> str | None:
    """Extract a *sanitised* client-supplied request id.

    A client-provided id is trusted only as a correlation hint, never as an
    identity or an authorisation signal, and it is length- and charset-limited
    so it cannot be used to inject log content or blow up log volume. If it does
    not look like an id, it is discarded and a server id is generated instead.
    """
    # Annotated explicitly: ``Scope`` is a plain mapping, so ``.get`` is ``Any``
    # and would silently defeat type checking for everything derived from it.
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers") or ()
    for raw_name, raw_value in headers:
        if raw_name == b"x-request-id":
            try:
                candidate = raw_value.decode("ascii")
            except UnicodeDecodeError:
                return None
            candidate = candidate.strip()
            if 8 <= len(candidate) <= 64 and all(c.isalnum() or c in "-_." for c in candidate):
                return candidate
            return None
    return None


class RequestContextMiddleware:
    """Pure ASGI middleware that binds a request id to logs and the response.

    Implemented at the ASGI layer rather than as ``BaseHTTPMiddleware`` so that
    it composes correctly with streaming responses and background tasks, and so
    that context variables are bound for the whole request without the known
    task-group pitfalls of the Starlette wrapper.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _client_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Re-raised: the exception handlers registered on the app own the
            # response. This block exists only so the access log still fires.
            logger.exception(
                "http_request_failed",
                method=method,
                path=path,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log = logger.info if status_code < 500 else logger.error
            log(
                "http_request",
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
        finally:
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered.

    Uses ``Content-Length`` when present. A chunked request without a length is
    not rejected here; endpoint-level limits and the reverse proxy remain the
    backstop, and this middleware is a cheap early exit for the common case.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    break
                if length > self.max_bytes:
                    await self._reject(scope, send)
                    return
                break

        await self.app(scope, receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        import orjson  # local import keeps module import light

        request_id = scope.get("state", {}).get("request_id")
        body: dict[str, Any] = {
            "error": "payload_too_large",
            "detail": f"Request body exceeds the {self.max_bytes} byte limit.",
        }
        if request_id:
            body["request_id"] = request_id
        payload = orjson.dumps(body)
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
