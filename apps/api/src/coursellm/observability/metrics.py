"""A tiny, dependency-free metrics registry serialised as Prometheus text.

Two decisions shape this module.

**No client library.** The exposition format is a handful of lines of text and
the recording API is two methods; a Prometheus or OTel SDK would add a
dependency, a background thread and an opinion about configuration in exchange
for code that fits on a screen. The registry is a process-local dictionary and
:meth:`MetricRegistry.render` produces the text ``GET /metrics`` returns.

**The label set is closed.** Every metric declares its labels in
:data:`METRIC_DEFINITIONS`, and a label is rejected unless the metric declares it
*and* it is a member of :data:`ALLOWED_LABELS`. Unbounded label cardinality is
how a metrics backend falls over: a per-``tenant_id`` or per-``request_id``
series multiplies the series count by the size of the population, turns a cheap
counter into a memory and cost problem, and is exactly the data the document
forbids on a metric. The same identifiers live on spans (sampled) and in logs
(joinable by ``trace_id``), where cardinality is not a cost problem.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

MetricType = Literal["counter", "histogram", "gauge"]


class MetricsError(Exception):
    """Base class for registry misuse."""


class UnknownMetricError(MetricsError):
    """Raised when a metric is recorded that the specification does not define."""


class ForbiddenLabelError(MetricsError):
    """Raised when a metric is given a label outside its closed label set."""


# ---------------------------------------------------------------------------
# The closed label universe (docs/architecture/observability.md section 5)
# ---------------------------------------------------------------------------
#: Labels a metric may use. ``timeout``, ``from_model`` and ``degraded`` are
#: included because the document's own metric table uses them
#: (``coursellm.rerank.duration``, ``coursellm.llm.fallback.total`` and
#: ``coursellm.retrieval.duration``) even though section 5's prose list omits
#: them; the table is the operative specification for a metric's labels.
ALLOWED_LABELS: frozenset[str] = frozenset(
    {
        "route",
        "method",
        "status_class",
        "intent",
        "outcome",
        "error_class",
        "stage",
        "retriever",
        "degraded",
        "model",
        "provider",
        "operation",
        "direction",
        "reason",
        "cache",
        "verdict",
        "class",
        "state",
        "dataset_version",
        "metric",
        "env",
        "service_version",
        "timeout",
        "from_model",
    }
)

#: Values that must never be a metric label. Each multiplies the series count by
#: a population size and belongs on a span or in a log instead.
FORBIDDEN_LABELS: frozenset[str] = frozenset(
    {
        "user_id",
        "tenant_id",
        "request_id",
        "trace_id",
        "span_id",
        "query",
        "query_text",
        "document_id",
        "course_id",
        "conversation_id",
        "chunk_id",
        "email",
        "url",
        "error_message",
        "exception",
    }
)


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One metric's name, type, labels and help text."""

    name: str
    metric_type: MetricType
    labels: frozenset[str]
    help: str


def _definition(
    name: str, metric_type: MetricType, labels: Iterable[str], help_text: str
) -> MetricDefinition:
    label_set = frozenset(labels)
    unknown = label_set - ALLOWED_LABELS
    if unknown:  # pragma: no cover - a programming error in this module
        msg = f"Metric {name!r} declares labels outside ALLOWED_LABELS: {sorted(unknown)}"
        raise MetricsError(msg)
    return MetricDefinition(name=name, metric_type=metric_type, labels=label_set, help=help_text)


#: Every metric the specification defines. The names and label sets are taken
#: verbatim from section 5's table.
METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    definition.name: definition
    for definition in (
        _definition(
            "http.server.request.duration",
            "histogram",
            ("route", "method", "status_class"),
            "HTTP request duration in milliseconds.",
        ),
        _definition(
            "coursellm.requests.total",
            "counter",
            ("route", "intent", "outcome"),
            "Requests handled, by route, routed intent and outcome.",
        ),
        _definition(
            "coursellm.errors.total",
            "counter",
            ("route", "error_class"),
            "Failed requests, by route and error class.",
        ),
        _definition(
            "coursellm.retrieval.duration",
            "histogram",
            ("stage", "degraded"),
            "Retrieval stage duration in milliseconds.",
        ),
        _definition(
            "coursellm.rerank.duration",
            "histogram",
            ("model", "timeout"),
            "Cross-encoder rerank duration in milliseconds.",
        ),
        _definition(
            "coursellm.llm.duration",
            "histogram",
            ("model", "provider", "operation"),
            "Model call duration in milliseconds.",
        ),
        _definition(
            "coursellm.request.duration",
            "histogram",
            ("route", "intent"),
            "End-to-end request duration in milliseconds.",
        ),
        _definition(
            "coursellm.llm.tokens",
            "counter",
            ("model", "direction"),
            "Tokens billed, by model and direction.",
        ),
        _definition(
            "coursellm.llm.cost_usd",
            "counter",
            ("model", "provider"),
            "Estimated model spend in US dollars.",
        ),
        _definition(
            "coursellm.llm.fallback.total",
            "counter",
            ("from_model", "reason"),
            "Calls answered by a fallback model.",
        ),
        _definition(
            "coursellm.llm.retries.total",
            "counter",
            ("model", "reason"),
            "Provider retries, by model and reason.",
        ),
        _definition(
            "coursellm.degraded.total",
            "counter",
            ("reason",),
            "Degradation paths taken, by machine-readable reason.",
        ),
        _definition(
            "coursellm.retrieval.candidates",
            "histogram",
            ("stage", "retriever"),
            "Candidates returned by a retriever stage.",
        ),
        _definition(
            "coursellm.rerank.score",
            "histogram",
            ("model",),
            "Reranker score distribution.",
        ),
        _definition(
            "coursellm.eval.score",
            "gauge",
            ("metric", "dataset_version"),
            "Evaluation metric value from the most recent run.",
        ),
        _definition(
            "coursellm.cache.hit_ratio",
            "counter",
            ("cache",),
            "Cache hits and misses, by cache.",
        ),
        _definition(
            "coursellm.graph.runs.active",
            "gauge",
            (),
            "Agent graph runs currently in flight.",
        ),
        _definition(
            "coursellm.graph.steps",
            "histogram",
            ("intent",),
            "Graph steps per run, by intent.",
        ),
        _definition(
            "coursellm.injection.verdicts",
            "counter",
            ("verdict", "class"),
            "Query safety verdicts, by verdict and class.",
        ),
        _definition(
            "coursellm.ingestion.jobs",
            "counter",
            ("state",),
            "Ingestion jobs, by state.",
        ),
    )
}

#: Bucket boundaries for every histogram. One set is enough at this scale and
#: keeps the exposition predictable; a duration in milliseconds and a token
#: count are both read as orders of magnitude.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
)

_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in labels.items()))


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in labels)
    return "{" + rendered + "}"


def _format_number(value: float) -> str:
    if value == int(value) and math.isfinite(value):
        return str(int(value))
    return repr(value)


class MetricRegistry:
    """A process-local counter/histogram/gauge store.

    Not thread-safe by design: the API is single-threaded per event loop and a
    lock on the hot path would cost more than it protects. A worker that needs
    to record from a thread constructs its own registry and merges it, rather
    than sharing this one.
    """

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._active_graph_runs = 0

    # -- validation ------------------------------------------------------
    @staticmethod
    def _validate(name: str, labels: Mapping[str, str]) -> MetricDefinition:
        definition = METRIC_DEFINITIONS.get(name)
        if definition is None:
            msg = (
                f"{name!r} is not a defined metric. Add it to METRIC_DEFINITIONS "
                f"(with a closed label set) before recording it."
            )
            raise UnknownMetricError(msg)
        for key in labels:
            if key in FORBIDDEN_LABELS or key not in ALLOWED_LABELS:
                msg = (
                    f"{key!r} is not an allowed label for {name!r}. The label set is "
                    f"closed and small; user_id, tenant_id, request_id, query and any "
                    f"free text are forbidden because unbounded cardinality is how a "
                    f"metrics backend falls over."
                )
                raise ForbiddenLabelError(msg)
            if key not in definition.labels:
                msg = (
                    f"{name!r} does not declare label {key!r}; its labels are "
                    f"{sorted(definition.labels)}."
                )
                raise ForbiddenLabelError(msg)
            if not _LABEL_NAME_RE.match(key):  # pragma: no cover - defensive
                msg = f"{key!r} is not a valid label name."
                raise ForbiddenLabelError(msg)
        return definition

    # -- recording -------------------------------------------------------
    def increment(self, name: str, value: float = 1.0, **labels: str) -> None:
        """Add ``value`` to a counter (or a gauge's running total)."""
        definition = self._validate(name, labels)
        key = (name, _label_key(labels))
        if definition.metric_type == "histogram":
            msg = f"{name!r} is a histogram; use observe()."
            raise MetricsError(msg)
        target = self._gauges if definition.metric_type == "gauge" else self._counters
        target[key] = target.get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Record one observation in a histogram."""
        definition = self._validate(name, labels)
        if definition.metric_type != "histogram":
            msg = f"{name!r} is a {definition.metric_type}; use increment()."
            raise MetricsError(msg)
        key = (name, _label_key(labels))
        self._histograms.setdefault(key, []).append(float(value))

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        """Set a gauge to an absolute value."""
        definition = self._validate(name, labels)
        if definition.metric_type != "gauge":
            msg = f"{name!r} is a {definition.metric_type}; use increment() or observe()."
            raise MetricsError(msg)
        self._gauges[(name, _label_key(labels))] = float(value)

    def set_active_graph_runs(self, value: int) -> None:
        self._gauges[("coursellm.graph.runs.active", ())] = float(value)

    # -- introspection ---------------------------------------------------
    def counter_value(self, name: str, **labels: str) -> float:
        self._validate(name, labels)
        return self._counters.get((name, _label_key(labels)), 0.0)

    def histogram_values(self, name: str, **labels: str) -> list[float]:
        self._validate(name, labels)
        return list(self._histograms.get((name, _label_key(labels)), []))

    def gauge_value(self, name: str, **labels: str) -> float:
        self._validate(name, labels)
        return self._gauges.get((name, _label_key(labels)), 0.0)

    def reset(self) -> None:
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()
        self._active_graph_runs = 0

    # -- exposition ------------------------------------------------------
    def render(self) -> str:
        """Render the registry as Prometheus text format.

        Every defined metric contributes a ``# HELP`` and ``# TYPE`` line even
        when it has no samples yet, so a new metric name is visible to a scrape
        immediately rather than only after the first event.
        """
        lines: list[str] = []
        for definition in METRIC_DEFINITIONS.values():
            lines.append(f"# HELP {definition.name} {definition.help}")
            lines.append(f"# TYPE {definition.name} {definition.metric_type}")
            if definition.metric_type == "histogram":
                lines.extend(self._render_histogram(definition))
            else:
                lines.extend(self._render_scalar(definition))
        return "\n".join(lines) + "\n"

    def _render_scalar(self, definition: MetricDefinition) -> list[str]:
        store = self._gauges if definition.metric_type == "gauge" else self._counters
        rendered: list[str] = []
        for (name, labels), value in sorted(store.items()):
            if name != definition.name:
                continue
            rendered.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
        return rendered

    def _render_histogram(self, definition: MetricDefinition) -> list[str]:
        rendered: list[str] = []
        for (name, labels), values in sorted(self._histograms.items()):
            if name != definition.name:
                continue
            total = sum(values)
            rendered.append(
                f"{name}_count{_format_labels(labels)} {_format_number(float(len(values)))}"
            )
            rendered.append(f"{name}_sum{_format_labels(labels)} {_format_number(total)}")
            for boundary in DEFAULT_BUCKETS:
                count = sum(1 for value in values if value <= boundary)
                bucket_labels = (*labels, ("le", _format_number(boundary)))
                rendered.append(
                    f"{name}_bucket{_format_labels(bucket_labels)} {_format_number(float(count))}"
                )
            infinity_labels = (*labels, ("le", "+Inf"))
            rendered.append(
                f"{name}_bucket{_format_labels(infinity_labels)} "
                f"{_format_number(float(len(values)))}"
            )
        return rendered


def percentile(values: Iterable[float], quantile: float) -> float:
    """The ``quantile`` (0..1) of ``values`` by linear interpolation.

    Matches the Prometheus/NumPy ``linear`` definition: the observation at
    ``(n - 1) * q`` in the sorted sample, interpolating between neighbours. A
    hand-computed test pins the arithmetic so the reported P95 is not an
    implementation-specific surprise.
    """
    if not 0.0 <= quantile <= 1.0:
        msg = f"quantile must be between 0 and 1, got {quantile!r}."
        raise ValueError(msg)
    sample = sorted(float(value) for value in values)
    if not sample:
        msg = "percentile() of an empty sample is undefined."
        raise ValueError(msg)
    if len(sample) == 1:
        return sample[0]
    position = (len(sample) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sample[lower]
    weight = position - lower
    return sample[lower] * (1.0 - weight) + sample[upper] * weight


# ---------------------------------------------------------------------------
# Process-wide registry and the recording helpers the instrumented layers use
# ---------------------------------------------------------------------------
_registry = MetricRegistry()


def get_registry() -> MetricRegistry:
    """The process-wide registry that ``GET /metrics`` renders."""
    return _registry


def reset_registry() -> None:
    """Clear every recorded series. For tests and for a configuration reload."""
    _registry.reset()


def record_http_request(
    *,
    route: str,
    method: str,
    status_code: int,
    duration_ms: float,
    intent: str | None = None,
    outcome: str | None = None,
    error_class: str | None = None,
) -> None:
    """Record the HTTP-level request metrics for one response."""
    status_class = f"{status_code // 100}xx"
    registry = get_registry()
    registry.observe(
        "http.server.request.duration",
        duration_ms,
        route=route,
        method=method,
        status_class=status_class,
    )
    registry.observe(
        "coursellm.request.duration",
        duration_ms,
        route=route,
        intent=intent or "unknown",
    )
    if intent is not None and outcome is not None:
        registry.increment("coursellm.requests.total", route=route, intent=intent, outcome=outcome)
    if error_class is not None:
        registry.increment("coursellm.errors.total", route=route, error_class=error_class)


def record_retrieval_stage(
    *,
    stage: str,
    duration_ms: float,
    candidates: int,
    retriever: str,
    degraded: bool,
) -> None:
    registry = get_registry()
    registry.observe(
        "coursellm.retrieval.duration",
        duration_ms,
        stage=stage,
        degraded=str(degraded).lower(),
    )
    registry.observe(
        "coursellm.retrieval.candidates",
        float(candidates),
        stage=stage,
        retriever=retriever,
    )


def record_rerank(
    *,
    model: str,
    duration_ms: float,
    timed_out: bool,
    scores: Iterable[float] = (),
) -> None:
    registry = get_registry()
    registry.observe(
        "coursellm.rerank.duration",
        duration_ms,
        model=model,
        timeout=str(timed_out).lower(),
    )
    for score in scores:
        registry.observe("coursellm.rerank.score", score, model=model)


def record_llm_call(
    *,
    model: str,
    provider: str,
    operation: str,
    duration_ms: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    fallback_used: bool,
    primary_model: str,
    retry_count: int,
    cache_hit: bool = False,
) -> None:
    registry = get_registry()
    registry.observe(
        "coursellm.llm.duration",
        duration_ms,
        model=model,
        provider=provider,
        operation=operation,
    )
    registry.increment("coursellm.llm.tokens", float(input_tokens), model=model, direction="in")
    registry.increment("coursellm.llm.tokens", float(output_tokens), model=model, direction="out")
    registry.increment("coursellm.llm.cost_usd", cost_usd, model=model, provider=provider)
    if fallback_used:
        registry.increment(
            "coursellm.llm.fallback.total",
            from_model=primary_model,
            reason="primary_failed",
        )
    if retry_count > 0:
        registry.increment("coursellm.llm.retries.total", model=model, reason="provider_error")
    registry.increment("coursellm.cache.hit_ratio", cache=(cache_hit and "hit") or "miss")


def record_degraded(*reasons: str) -> None:
    registry = get_registry()
    for reason in reasons:
        if reason:
            registry.increment("coursellm.degraded.total", reason=reason)


def record_graph_run(*, intent: str, steps: int, outcome: str) -> None:
    registry = get_registry()
    registry.observe("coursellm.graph.steps", float(steps), intent=intent)
    registry.increment(
        "coursellm.requests.total",
        route="graph",
        intent=intent,
        outcome=outcome,
    )


__all__ = [
    "ALLOWED_LABELS",
    "DEFAULT_BUCKETS",
    "FORBIDDEN_LABELS",
    "METRIC_DEFINITIONS",
    "ForbiddenLabelError",
    "MetricDefinition",
    "MetricRegistry",
    "MetricsError",
    "UnknownMetricError",
    "get_registry",
    "percentile",
    "record_degraded",
    "record_graph_run",
    "record_http_request",
    "record_llm_call",
    "record_rerank",
    "record_retrieval_stage",
    "reset_registry",
]
