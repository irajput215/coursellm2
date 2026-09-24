"""Observability: tracing, metrics and the optional LangSmith integration.

The package is deliberately import-light: importing it installs no exporter,
opens no socket and imports no vendor SDK. :func:`configure_tracing` and
:func:`configure_langsmith` are the only entry points that construct anything,
and both are no-ops unless configuration asks for them.

``RequestMetricsMiddleware`` lives here rather than in the API layer so the
metric names and label values are defined once, next to the registry that
validates them.
"""

from __future__ import annotations

import time
from typing import Any

from coursellm.observability import attributes, langsmith, metrics, tracing
from coursellm.observability.attributes import (
    ALLOWED_ATTRIBUTE_NAMES,
    ATTRIBUTE_NAMES,
    FORBIDDEN_ATTRIBUTE_SUBSTRINGS,
)
from coursellm.observability.langsmith import LangSmithHandle, configure_langsmith
from coursellm.observability.metrics import get_registry, reset_registry
from coursellm.observability.tracing import (
    TracingHandle,
    configure_tracing,
    current_trace_id,
    instrument_fastapi,
    shutdown_tracing,
    span,
)

__all__ = [
    "ALLOWED_ATTRIBUTE_NAMES",
    "ATTRIBUTE_NAMES",
    "FORBIDDEN_ATTRIBUTE_SUBSTRINGS",
    "LangSmithHandle",
    "RequestMetricsMiddleware",
    "TracingHandle",
    "attributes",
    "configure_langsmith",
    "configure_tracing",
    "current_trace_id",
    "get_registry",
    "instrument_fastapi",
    "langsmith",
    "metrics",
    "reset_registry",
    "shutdown_tracing",
    "span",
    "tracing",
]


class RequestMetricsMiddleware:
    """Record HTTP request metrics with a bounded label set.

    A pure ASGI middleware for the same reason the request-context middleware is
    one: it composes with streaming responses. The route label is the matched
    route template (``/api/v1/chat``), never the raw path, so a path parameter
    cannot explode the series count.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Any) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # The matched route template, never the raw path: a 404 path is
            # attacker-controlled and would otherwise be an unbounded label
            # value dressed up as a bounded one.
            route_object = scope.get("route")
            route = str(getattr(route_object, "path", "unmatched"))
            metrics.record_http_request(
                route=route,
                method=str(scope.get("method", "-")),
                status_code=status_code,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                error_class="server_error" if status_code >= 500 else None,
            )
