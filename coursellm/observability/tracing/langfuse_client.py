from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from core.config import settings
from observability.event_store import append_event
from observability.context import (
    get_course_id,
    get_request_id,
    get_trace_id,
    get_user_id,
    set_trace_id,
)

logger = logging.getLogger("coursellm.observability")


class NoOpSpan:
    trace_id: str | None = None
    observation_id: str | None = None

    def update(self, **_kwargs: Any) -> "NoOpSpan":
        return self

    def end(self, **_kwargs: Any) -> "NoOpSpan":
        return self


def is_tracing_enabled() -> bool:
    if not settings.OBSERVABILITY_ENABLED:
        return False
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


def _string_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    base: dict[str, str] = {}
    request_id = get_request_id()
    user_id = get_user_id()
    course_id = get_course_id()

    if request_id:
        base["request_id"] = request_id
    if user_id is not None:
        base["user_id"] = str(user_id)
    if course_id is not None:
        base["course_id"] = str(course_id)

    if metadata:
        for key, value in metadata.items():
            if value is not None:
                base[key] = str(value)

    return base


_langfuse_client = None


def _get_client():
    global _langfuse_client
    if _langfuse_client is None:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_HOST,
        )
    return _langfuse_client


@contextmanager
def trace_span(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    if not is_tracing_enabled():
        yield NoOpSpan()
        return

    from langfuse import propagate_attributes

    client = _get_client()
    meta = _string_metadata(metadata)
    start = time.perf_counter()

    propagation_kwargs: dict[str, Any] = {"metadata": meta}
    user_id = get_user_id()
    request_id = get_request_id()
    if user_id is not None:
        propagation_kwargs["user_id"] = str(user_id)
    if request_id:
        propagation_kwargs["session_id"] = request_id

    with propagate_attributes(**propagation_kwargs):
        with client.start_as_current_observation(
            name=name,
            as_type=as_type,  # type: ignore[arg-type]
            input=input,
            metadata=meta,
        ) as span:
            if span.trace_id:
                set_trace_id(span.trace_id)
            try:
                yield span
            finally:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                span.update(metadata={**meta, "latency_ms": str(latency_ms)})


def log_pipeline_event(event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "request_id": get_request_id(),
        "event": event,
        "user_id": get_user_id(),
        "course_id": get_course_id(),
        "trace_id": get_trace_id(),
    }
    payload.update(fields)
    logger.info(json.dumps(payload, default=str))
    if settings.OBSERVABILITY_DEBUG_API_ENABLED:
        append_event(payload)


def flush_traces() -> None:
    if not is_tracing_enabled():
        return

    _get_client().flush()
