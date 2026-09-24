"""OpenTelemetry provider setup, span helpers and DB instrumentation.

The module has one job: make a span cost nothing when tracing is off, so call
sites never need a conditional. Importing it opens no sockets and constructs no
exporter; :func:`configure_tracing` is the only thing that does, and only when
``OTEL_ENABLED`` is true and the exporter is not ``none``.

Sampling follows ``docs/architecture/observability.md`` section 3. The sampler is
head-based and parent-based — a child is sampled iff its root was — with the
document's per-route ratios and the always-sample-errors rule applied where the
outcome is knowable at span start (a trace marked as errored by
:func:`mark_trace_error`, e.g. from an error-handling middleware that has already
classified the failure). The honest caveat from the document applies: a request
that fails *after* the head decision will have its HTTP span kept only if the
route was sampled, because head sampling cannot see the future.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    Decision,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import (
    Span,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
    get_current_span,
)

from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.observability.attributes import (
    DB_COLLECTION_NAME,
    DB_OPERATION_NAME,
    DB_QUERY_SUMMARY,
    DB_RLS_ACTIVE,
    DB_ROWS_AFFECTED,
    DB_SYSTEM,
    DB_TENANT_SCOPED,
    SPAN_DB_QUERY,
    is_forbidden_payload_key,
    sanitize_attributes,
)

logger = get_logger(__name__)

#: Trace-state key used to propagate "this trace failed" across a process
#: boundary, so a downstream service samples a trace it would otherwise drop.
ERROR_TRACE_STATE_KEY = "coursellm.error"

#: Routes whose volume has no diagnostic value (section 3).
_NEVER_SAMPLE_PATHS = ("/healthz", "/readyz", "/metrics")
#: The path that carries latency and cost; worth a higher rate than the base.
_CHAT_PATH = "/api/v1/chat"
_INGESTION_PATH = "/api/v1/documents"

_SQL_OPERATION_RE = re.compile(r"^\s*([A-Za-z]+)")
_SQL_COLLECTION_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.IGNORECASE
)
_SQL_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

_DB_SPANS_KEY = "_coursellm_db_spans"


# ---------------------------------------------------------------------------
# Provider setup
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TracingHandle:
    """What :func:`configure_tracing` returns and :func:`shutdown_tracing` closes.

    ``enabled=False`` with no provider is the complete no-op: nothing was
    constructed, nothing is installed, and :func:`span` yields a recorder that
    discards everything.
    """

    enabled: bool
    provider: TracerProvider | None = None
    tracer: trace.Tracer | None = None
    exporter: SpanExporter | None = None
    db_instrumented: bool = False


@dataclass(slots=True)
class _State:
    active: TracingHandle | None = None
    test_exporter: SpanExporter | None = None


_state = _State()


def set_test_span_exporter(exporter: SpanExporter | None) -> None:
    """Install an exporter for the next :func:`configure_tracing`. Tests only."""
    _state.test_exporter = exporter


def reset_tracing() -> None:
    """Drop the active handle and any test exporter. Tests only."""
    _state.active = None
    _state.test_exporter = None


def active_handle() -> TracingHandle | None:
    return _state.active


def _build_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            SERVICE_NAME: settings.otel_service_name,
            "service.version": _service_version(),
            "deployment.environment": settings.environment.value,
            "coursellm.config_version": settings.retrieval_config_version,
        }
    )


def _service_version() -> str:
    from coursellm import __version__

    return __version__


def _build_exporter(settings: Settings) -> SpanExporter:
    if settings.otel_traces_exporter == "console":
        return ConsoleSpanExporter()
    # Constructed only after the enabled check in configure_tracing, so the
    # disabled path never touches the network stack.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)


def _ratio_for_span(name: str, default_ratio: float) -> float:
    """The document's per-route ratio, relative to the configured base rate.

    The table in section 3 is expressed against ``OTEL_SAMPLE_RATIO=0.1``
    (chat at twice the base, read-only metadata at half, ingestion at 1.0).
    Multiplying the configured base keeps that relationship when a deployment
    tunes the base rate, and a base of ``1.0`` — the local-development setting —
    means every trace is kept rather than being capped at a route override.
    """
    path = name.split(" ", 1)[1] if " " in name else name
    if path in _NEVER_SAMPLE_PATHS or path.rstrip("/") in _NEVER_SAMPLE_PATHS:
        return 0.0
    if default_ratio >= 1.0:
        return 1.0
    if path.startswith(_CHAT_PATH):
        return min(1.0, default_ratio * 2.0)
    if path.startswith(_INGESTION_PATH):
        return 1.0
    if path.startswith("/api/v1/"):
        return default_ratio * 0.5
    return default_ratio


def _is_error_marked(trace_state: TraceState | None) -> bool:
    return trace_state is not None and trace_state.get(ERROR_TRACE_STATE_KEY) == "1"


class CourseLLMSampler:
    """Parent-based ratio sampling with route overrides and error bias.

    * a child follows its parent's decision, which is what makes a trace
      internally consistent rather than a random subset of its own spans;
    * a root uses the route ratio from section 3;
    * a trace whose ``tracestate`` carries ``coursellm.error=1`` is always kept,
      which is the cross-process half of always-sample-errors.
    """

    def __init__(self, default_ratio: float) -> None:
        self._default_ratio = default_ratio

    def should_sample(
        self,
        parent_context: Any,
        trace_id: int,
        name: str,
        kind: Any = None,
        attributes: Mapping[str, Any] | None = None,
        links: Any = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        parent_span = (
            get_current_span(parent_context) if parent_context is not None else get_current_span()
        )
        parent = parent_span.get_span_context()
        if parent is not None and parent.is_valid:
            if parent.trace_flags & TraceFlags.SAMPLED:
                return SamplingResult(
                    Decision.RECORD_AND_SAMPLE,
                    attributes=attributes,
                    trace_state=trace_state,
                )
            if _is_error_marked(trace_state):
                return SamplingResult(
                    Decision.RECORD_AND_SAMPLE,
                    attributes=attributes,
                    trace_state=trace_state,
                )
            return SamplingResult(Decision.DROP, attributes=attributes, trace_state=trace_state)

        if _is_error_marked(trace_state):
            return SamplingResult(
                Decision.RECORD_AND_SAMPLE, attributes=attributes, trace_state=trace_state
            )

        ratio = _ratio_for_span(name, self._default_ratio) if name else self._default_ratio
        if ratio <= 0.0:
            return SamplingResult(Decision.DROP, attributes=attributes, trace_state=trace_state)
        if ratio >= 1.0:
            return SamplingResult(
                Decision.RECORD_AND_SAMPLE, attributes=attributes, trace_state=trace_state
            )
        return TraceIdRatioBased(ratio).should_sample(
            parent_context,
            trace_id,
            name,
            kind,
            attributes,
            links,
            trace_state,
        )

    def get_description(self) -> str:
        return f"CourseLLMSampler{{default={self._default_ratio}}}"


def _build_sampler(default_ratio: float) -> Any:
    """The sampler, typed as ``Any`` for the SDK's inconsistent stubs.

    ``opentelemetry-sdk`` exposes ``Sampler`` as an untyped protocol in some
    stub versions and a concrete class in others; returning ``Any`` here keeps
    the call site honest without a cast at the provider.
    """
    return CourseLLMSampler(default_ratio)


def configure_tracing(
    settings: Settings, *, span_exporter: SpanExporter | None = None
) -> TracingHandle:
    """Install the tracer provider, or return a no-op handle.

    The disabled path constructs no provider and no exporter. When enabled, the
    OTLP HTTP exporter is wrapped in a :class:`BatchSpanProcessor` so a request
    never blocks on export, and the SQLAlchemy query listener is registered so
    repository queries become child spans automatically.
    """
    if _state.active is not None and _state.active.enabled:
        return _state.active

    if not settings.otel_enabled or settings.otel_traces_exporter == "none":
        return TracingHandle(enabled=False)

    exporter = span_exporter or _state.test_exporter or _build_exporter(settings)
    provider = TracerProvider(
        resource=_build_resource(settings),
        sampler=_build_sampler(settings.otel_sample_ratio),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    # The global provider is deliberately *not* replaced. Every instrumentor
    # this application installs receives the provider explicitly, and
    # ``opentelemetry.trace.set_tracer_provider`` is process-global and
    # once-only, which would make a second configuration (a second app in a test
    # process, a settings reload) silently keep the first provider and its
    # already-shut-down exporter.

    if settings.capture_prompts_in_traces:
        # A data-protection decision, not a debugging toggle: enabling it writes
        # student document content and query text to the trace backend.
        logger.warning(
            "prompt_capture_enabled",
            impact=(
                "Prompt and completion content will be attached to spans. The tracing "
                "backend now processes student content; confirm its retention and "
                "access controls, or set CAPTURE_PROMPTS_IN_TRACES=false."
            ),
        )

    handle = TracingHandle(
        enabled=True,
        provider=provider,
        tracer=provider.get_tracer("coursellm"),
        exporter=exporter,
    )
    _state.active = handle
    _instrument_sqlalchemy(handle)
    return handle


def shutdown_tracing(handle: TracingHandle | None) -> None:
    """Flush and stop the provider. Safe to call with a no-op handle."""
    if handle is None or not handle.enabled or handle.provider is None:
        if _state.active is not None and _state.active.enabled:
            _state.active = None
        return
    _uninstrument_sqlalchemy()
    try:
        handle.provider.shutdown()
    finally:
        if _state.active is handle:
            _state.active = None


def mark_trace_error() -> None:
    """Mark the active span as errored so a sampler/collector can keep it.

    Head sampling cannot see an outcome that has not happened, so the
    always-sample-errors guarantee is applied where the outcome is knowable: a
    trace whose incoming ``tracestate`` carries :data:`ERROR_TRACE_STATE_KEY` is
    always kept, and a span marked here carries the error classification its
    collector needs. The document's caveat applies: a request that fails after
    the head decision has its HTTP span kept only if the route was sampled.
    """
    current = get_current_span()
    span_context = current.get_span_context()
    if span_context is None or not span_context.is_valid:  # pragma: no cover - no active span
        return
    current.set_attribute("coursellm.error", True)
    current.set_status(Status(StatusCode.ERROR))


def instrument_fastapi(app: Any, handle: TracingHandle) -> None:
    """Attach the FastAPI instrumentor so each request gets a server span."""
    if not handle.enabled or handle.provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=handle.provider,
        excluded_urls="healthz,readyz,metrics",
    )


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------
@runtime_checkable
class SpanRecorder(Protocol):
    """The subset of the span API call sites may use.

    A protocol rather than the concrete ``Span`` type so the no-op recorder can
    stand in when tracing is disabled, and so a call site cannot reach for
    ``add_event``-style methods that carry arbitrary payloads.
    """

    def set_attribute(self, key: str, value: Any) -> None: ...

    def set_attributes(self, attributes: Mapping[str, Any]) -> None: ...

    def record_exception(self, exception: BaseException) -> None: ...

    def set_error(self) -> None: ...


class _NoopRecorder:
    """Discards every call. Used when tracing is disabled."""

    __slots__ = ()

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        return None

    def record_exception(self, exception: BaseException) -> None:
        return None

    def set_error(self) -> None:
        return None


class _OtelRecorder:
    """Writes to a live span, applying the redaction policy on the way in."""

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        if is_forbidden_payload_key(key):
            return
        self._span.set_attribute(key, value)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def record_exception(self, exception: BaseException) -> None:
        self._span.record_exception(exception)

    def set_error(self) -> None:
        self._span.set_status(Status(StatusCode.ERROR))


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[SpanRecorder]:
    """Open a child span for the duration of the block.

    When tracing is off this yields a recorder that discards everything, so a
    call site is unconditionally ``with span(...) as record:``. Any attribute
    whose key looks like payload is dropped by :func:`_OtelRecorder.set_attribute`;
    the block is redacted at the boundary, not by convention.
    """
    handle = _state.active
    if handle is None or not handle.enabled or handle.tracer is None:
        yield _NoopRecorder()
        return

    with handle.tracer.start_as_current_span(name) as otel_span:
        recorder = _OtelRecorder(otel_span)
        recorder.set_attributes(sanitize_attributes(attributes))
        try:
            yield recorder
        except BaseException as exc:
            recorder.record_exception(exc)
            recorder.set_error()
            raise


def current_trace_id() -> str | None:
    """The active trace id as 32 lowercase hex characters, or ``None``."""
    span_context = get_current_span().get_span_context()
    if span_context is None or not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


def current_span_id() -> str | None:
    """The active span id as 16 lowercase hex characters, or ``None``."""
    span_context = get_current_span().get_span_context()
    if span_context is None or not span_context.is_valid:
        return None
    return format(span_context.span_id, "016x")


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` header for the active span, or ``None``.

    LangSmith and OTel traces are correlated by trace id; the header is what
    carries that id across a process or vendor boundary.
    """
    span_context = get_current_span().get_span_context()
    if span_context is None or not span_context.is_valid:
        return None
    return (
        f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-"
        f"{int(span_context.trace_flags):02x}"
    )


def config_version_attribute(settings: Settings) -> dict[str, str]:
    """Convenience for a call site that only has a settings object."""
    from coursellm.observability.attributes import COURSELLM_CONFIG_VERSION

    return {COURSELLM_CONFIG_VERSION: settings.retrieval_config_version}


# ---------------------------------------------------------------------------
# Database instrumentation
# ---------------------------------------------------------------------------
def _operation(statement: str) -> str:
    match = _SQL_OPERATION_RE.match(statement)
    return match.group(1).upper() if match else "OTHER"


def _collection(statement: str) -> str:
    match = _SQL_COLLECTION_RE.search(statement)
    return match.group(1) if match else "unknown"


def _summarize(statement: str) -> str:
    """A parameterised, redacted query summary.

    SQLAlchemy sends bound parameters out of band, so the statement itself
    carries placeholders. String literals are still replaced defensively: a
    driver or a ``literal_binds`` call site could inline a value, and a value is
    exactly what must never become a document attribute.
    """
    collapsed = " ".join(statement.split())
    redacted = _SQL_STRING_LITERAL_RE.sub("?", collapsed)
    return redacted[:200]


def _before_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    handle = _state.active
    if handle is None or handle.tracer is None:
        return
    info = getattr(conn, "info", None)
    if info is None:  # pragma: no cover - connection type without .info
        return
    query_span = handle.tracer.start_span(SPAN_DB_QUERY)
    tenant_scoped = "tenant_id" in statement
    query_span.set_attribute(DB_SYSTEM, "postgresql")
    query_span.set_attribute(DB_OPERATION_NAME, _operation(statement))
    query_span.set_attribute(DB_COLLECTION_NAME, _collection(statement))
    query_span.set_attribute(DB_QUERY_SUMMARY, _summarize(statement))
    query_span.set_attribute(DB_TENANT_SCOPED, tenant_scoped)
    query_span.set_attribute(DB_RLS_ACTIVE, tenant_scoped)
    info.setdefault(_DB_SPANS_KEY, []).append(query_span)


def _after_cursor_execute(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    info = getattr(conn, "info", None)
    if info is None:  # pragma: no cover - connection type without .info
        return
    spans: list[Span] = info.get(_DB_SPANS_KEY) or []
    if not spans:
        return
    query_span = spans.pop()
    rowcount = getattr(cursor, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0:
        query_span.set_attribute(DB_ROWS_AFFECTED, rowcount)
    query_span.end()


def _instrument_sqlalchemy(handle: TracingHandle) -> None:
    """Turn every repository query into a child span, without editing a repository."""
    if handle.db_instrumented:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(Engine, "after_cursor_execute", _after_cursor_execute)
    handle.db_instrumented = True


def _uninstrument_sqlalchemy() -> None:
    handle = _state.active
    if handle is None or not handle.db_instrumented:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    event.remove(Engine, "before_cursor_execute", _before_cursor_execute)
    event.remove(Engine, "after_cursor_execute", _after_cursor_execute)
    handle.db_instrumented = False


__all__ = [
    "ERROR_TRACE_STATE_KEY",
    "CourseLLMSampler",
    "SpanRecorder",
    "TracingHandle",
    "active_handle",
    "config_version_attribute",
    "configure_tracing",
    "current_span_id",
    "current_trace_id",
    "current_traceparent",
    "instrument_fastapi",
    "mark_trace_error",
    "reset_tracing",
    "set_test_span_exporter",
    "shutdown_tracing",
    "span",
]
