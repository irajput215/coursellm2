"""Unit tests for the metrics registry's closed label set and exposition.

The registry is the enforcement point for the document's high-cardinality rule:
a metric label that is an unbounded value turns a cheap counter into a memory
and cost problem in the metrics backend. These tests assert that the rule is a
rejection, not a convention, and pin the histogram arithmetic used to report
percentiles.
"""

from __future__ import annotations

import pytest

from coursellm.observability import metrics as metrics_module
from coursellm.observability.metrics import (
    ALLOWED_LABELS,
    FORBIDDEN_LABELS,
    METRIC_DEFINITIONS,
    ForbiddenLabelError,
    MetricRegistry,
    UnknownMetricError,
    percentile,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def registry() -> MetricRegistry:
    return MetricRegistry()


class TestClosedLabelSet:
    def test_every_metric_label_is_in_the_documented_allowlist(self) -> None:
        for definition in METRIC_DEFINITIONS.values():
            assert definition.labels <= ALLOWED_LABELS, (
                f"{definition.name} declares labels outside the documented allowlist: "
                f"{sorted(definition.labels - ALLOWED_LABELS)}"
            )

    def test_no_metric_declares_a_forbidden_label(self) -> None:
        for definition in METRIC_DEFINITIONS.values():
            assert not (definition.labels & FORBIDDEN_LABELS), (
                f"{definition.name} declares a forbidden high-cardinality label: "
                f"{sorted(definition.labels & FORBIDDEN_LABELS)}"
            )

    @pytest.mark.parametrize(
        "label",
        ["user_id", "tenant_id", "request_id", "trace_id", "query", "query_text", "email"],
    )
    def test_registry_rejects_a_forbidden_label(self, registry: MetricRegistry, label: str) -> None:
        with pytest.raises(ForbiddenLabelError):
            registry.increment(
                "coursellm.requests.total",
                route="/api/v1/chat",
                intent="tutor",
                outcome="answered",
                **{label: "value"},
            )

    def test_registry_rejects_a_label_the_metric_does_not_declare(
        self, registry: MetricRegistry
    ) -> None:
        with pytest.raises(ForbiddenLabelError):
            registry.increment("coursellm.degraded.total", reason="x", stage="semantic")

    def test_registry_rejects_an_undefined_metric(self, registry: MetricRegistry) -> None:
        with pytest.raises(UnknownMetricError):
            registry.increment("coursellm.made.up", reason="x")

    def test_too_many_labels_is_rejected(self, registry: MetricRegistry) -> None:
        with pytest.raises(ForbiddenLabelError):
            registry.increment(
                "coursellm.degraded.total",
                reason="reranker_unavailable",
                model="gpt-4o-mini",
            )


class TestRecording:
    def test_counter_increments_accumulate(self, registry: MetricRegistry) -> None:
        registry.increment(
            "coursellm.requests.total", route="/api/v1/chat", intent="tutor", outcome="answered"
        )
        registry.increment(
            "coursellm.requests.total",
            2.0,
            route="/api/v1/chat",
            intent="tutor",
            outcome="answered",
        )
        assert (
            registry.counter_value(
                "coursellm.requests.total",
                route="/api/v1/chat",
                intent="tutor",
                outcome="answered",
            )
            == 3.0
        )

    def test_histogram_observations_are_kept(self, registry: MetricRegistry) -> None:
        registry.observe("coursellm.retrieval.duration", 1.5, stage="semantic", degraded="false")
        registry.observe("coursellm.retrieval.duration", 2.5, stage="semantic", degraded="false")
        values = registry.histogram_values(
            "coursellm.retrieval.duration", stage="semantic", degraded="false"
        )
        assert values == [1.5, 2.5]

    def test_increment_on_a_histogram_is_an_error(self, registry: MetricRegistry) -> None:
        with pytest.raises(metrics_module.MetricsError):
            registry.increment("coursellm.llm.duration", model="m", provider="p", operation="chat")

    def test_observe_on_a_counter_is_an_error(self, registry: MetricRegistry) -> None:
        with pytest.raises(metrics_module.MetricsError):
            registry.observe("coursellm.requests.total", 1.0, route="/x", intent="i", outcome="o")


class TestPercentiles:
    def test_median_of_an_even_sample_interpolates(self) -> None:
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5

    def test_p95_of_three_observations_interpolates(self) -> None:
        # position = (3 - 1) * 0.95 = 1.9 -> 20 * 0.1 + 30 * 0.9 = 29.0
        assert percentile([10.0, 20.0, 30.0], 0.95) == pytest.approx(29.0)

    def test_single_observation_is_returned_unchanged(self) -> None:
        assert percentile([42.0], 0.95) == 42.0

    def test_quantiles_are_ordered_and_bounded(self) -> None:
        sample = [5.0, 1.0, 9.0, 3.0, 7.0]
        assert percentile(sample, 0.0) == 1.0
        assert percentile(sample, 1.0) == 9.0
        assert percentile(sample, 0.25) <= percentile(sample, 0.75)

    def test_empty_sample_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 0.5)

    def test_quantile_outside_zero_to_one_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            percentile([1.0], 1.5)


class TestExposition:
    def test_render_declares_every_documented_metric(self, registry: MetricRegistry) -> None:
        body = registry.render()
        for name in METRIC_DEFINITIONS:
            assert f"# HELP {name} " in body
            assert f"# TYPE {name} " in body

    def test_render_contains_counter_and_histogram_samples(self, registry: MetricRegistry) -> None:
        registry.increment(
            "coursellm.requests.total", route="/api/v1/chat", intent="tutor", outcome="answered"
        )
        registry.observe("coursellm.retrieval.duration", 3.0, stage="fusion", degraded="false")
        body = registry.render()
        assert (
            'coursellm.requests.total{intent="tutor",outcome="answered",route="/api/v1/chat"} 1'
            in body
        )
        assert 'coursellm.retrieval.duration_count{degraded="false",stage="fusion"} 1' in body
        assert 'coursellm.retrieval.duration_sum{degraded="false",stage="fusion"} 3' in body

    def test_render_never_contains_a_tenant_identifier(self, registry: MetricRegistry) -> None:
        tenant_id = "6f4d3c2b-1a09-4f8e-9d7c-6b5a4c3d2e1f"
        registry.increment(
            "coursellm.requests.total", route="/api/v1/chat", intent="tutor", outcome="answered"
        )
        assert tenant_id not in registry.render()

    def test_reset_clears_the_registry(self, registry: MetricRegistry) -> None:
        registry.increment(
            "coursellm.requests.total", route="/api/v1/chat", intent="tutor", outcome="answered"
        )
        registry.reset()
        assert (
            registry.counter_value(
                "coursellm.requests.total",
                route="/api/v1/chat",
                intent="tutor",
                outcome="answered",
            )
            == 0.0
        )
