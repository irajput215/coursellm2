"""Hand-computed unit tests for the native retrieval, citation and system metrics.

Every retrieval function has at least one worked example whose value is computed
by hand in the test, plus the degenerate cases: ``k`` larger than the candidate
list, no relevant documents, all relevant, and a single candidate.
"""

from __future__ import annotations

import math

import pytest
from evals.metrics.citations import citation_metrics, citation_metrics_from_answer
from evals.metrics.retrieval import (
    context_precision,
    context_recall,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evals.metrics.system import (
    TokenUsage,
    degradation_histogram,
    error_rate,
    fallback_rate,
    latency_percentiles,
    percentile,
    rate,
    token_summary,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# recall_at_k
# ---------------------------------------------------------------------------
def test_recall_at_k_hand_computed() -> None:
    # relevant = {b, d}; top-2 of [a, b, c, d] contains only b -> 1/2.
    assert recall_at_k(["a", "b", "c", "d"], {"b", "d"}, 2) == pytest.approx(0.5)
    assert recall_at_k(["a", "b", "c", "d"], {"b", "d"}, 4) == pytest.approx(1.0)


def test_recall_at_k_handles_degenerate_cases() -> None:
    # k larger than the candidate list uses the whole list.
    assert recall_at_k(["a", "b"], {"b", "c"}, 10) == pytest.approx(0.5)
    # No relevant documents: 0.0, not a vacuous 1.0.
    assert recall_at_k(["a", "b"], set(), 2) == 0.0
    # k <= 0 retrieves nothing.
    assert recall_at_k(["a", "b"], {"a"}, 0) == 0.0
    # All relevant.
    assert recall_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)
    # A single candidate.
    assert recall_at_k(["a"], {"a"}, 1) == pytest.approx(1.0)
    assert recall_at_k(["a"], {"b"}, 1) == 0.0


# ---------------------------------------------------------------------------
# precision_at_k
# ---------------------------------------------------------------------------
def test_precision_at_k_hand_computed() -> None:
    # top-2 of [a, b, c, d] contains only b -> 1/2.
    assert precision_at_k(["a", "b", "c", "d"], {"b", "d"}, 2) == pytest.approx(0.5)
    assert precision_at_k(["a", "b", "c", "d"], {"b", "d"}, 3) == pytest.approx(1 / 3)


def test_precision_at_k_handles_degenerate_cases() -> None:
    # A short list divides by its actual length, not by k.
    assert precision_at_k(["a", "b"], {"a"}, 10) == pytest.approx(0.5)
    assert precision_at_k(["a", "b"], set(), 2) == 0.0
    assert precision_at_k(["a", "b"], {"a"}, 0) == 0.0
    assert precision_at_k([], {"a"}, 5) == 0.0
    # All relevant.
    assert precision_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)
    # A single candidate.
    assert precision_at_k(["a"], {"a"}, 1) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# mrr
# ---------------------------------------------------------------------------
def test_mrr_hand_computed() -> None:
    assert mrr(["a", "b", "c"], {"a"}) == pytest.approx(1.0)
    assert mrr(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert mrr(["a", "b", "c", "d"], {"d"}) == pytest.approx(0.25)


def test_mrr_handles_degenerate_cases() -> None:
    assert mrr(["a", "b", "c"], {"z"}) == 0.0
    assert mrr([], {"a"}) == 0.0
    # A bounded window can exclude the only relevant item.
    assert mrr(["a", "b", "c"], {"c"}, k=2) == 0.0
    assert mrr(["a", "b", "c"], {"b"}, k=2) == pytest.approx(0.5)
    # A single candidate.
    assert mrr(["b"], {"b"}) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# ndcg_at_k
# ---------------------------------------------------------------------------
def test_ndcg_at_k_hand_computed() -> None:
    # retrieved [a, b, c, d], relevant {a, c}, k=4
    # DCG  = 1/log2(2) + 0 + 1/log2(4) + 0 = 1 + 0.5 = 1.5
    # IDCG = 1/log2(2) + 1/log2(3)
    expected = 1.5 / (1.0 / math.log2(2) + 1.0 / math.log2(3))
    assert ndcg_at_k(["a", "b", "c", "d"], {"a", "c"}, 4) == pytest.approx(expected)
    assert ndcg_at_k(["a", "b", "c", "d"], {"a", "c"}, 4) == pytest.approx(0.9197207891481876)


def test_ndcg_at_k_handles_degenerate_cases() -> None:
    # Perfect ranking is 1.0.
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)
    # Relevant set empty -> 0.0.
    assert ndcg_at_k(["a", "b"], set(), 2) == 0.0
    # The only relevant item is beyond k -> 0.0.
    assert ndcg_at_k(["a", "b"], {"b"}, 1) == 0.0
    # k larger than the list: IDCG still covers both relevant items.
    expected = 1.0 / (1.0 / math.log2(2) + 1.0 / math.log2(3))
    assert ndcg_at_k(["a"], {"a", "b"}, 5) == pytest.approx(expected)
    # A single candidate that is relevant.
    assert ndcg_at_k(["a"], {"a"}, 1) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# context_precision / context_recall
# ---------------------------------------------------------------------------
def test_context_precision_hand_computed() -> None:
    # Relevant at ranks 1 and 3 of three: (1/1 + 2/3) / 2 = 5/6.
    assert context_precision(["a", "b", "c"], {"a", "c"}, 3) == pytest.approx(5 / 6)
    # A single relevant item at rank 3: precision@3 = 1/3.
    assert context_precision(["a", "b", "c"], {"c"}, 3) == pytest.approx(1 / 3)


def test_context_precision_handles_degenerate_cases() -> None:
    assert context_precision(["a", "b"], {"z"}, 2) == 0.0
    assert context_precision(["a", "b"], set(), 2) == 0.0
    assert context_precision(["a", "b"], {"a"}, 0) == 0.0
    assert context_precision(["a"], {"a"}, 1) == pytest.approx(1.0)


def test_context_recall_matches_recall_arithmetic() -> None:
    retrieved = ["a", "b", "c", "d"]
    relevant = {"b", "d"}
    assert context_recall(retrieved, relevant, 2) == pytest.approx(0.5)
    assert context_recall(retrieved, relevant, 4) == pytest.approx(1.0)
    assert context_recall(retrieved, set(), 4) == 0.0


# ---------------------------------------------------------------------------
# citation metrics
# ---------------------------------------------------------------------------
def test_citation_metrics_hand_computed() -> None:
    metrics = citation_metrics(
        cited=["S1", "S2", "S9"],
        offered={"S1", "S2"},
        relevant_offered={"S1"},
    )
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(1.0)
    assert metrics.hallucination_rate == pytest.approx(1 / 3)
    assert (metrics.cited, metrics.valid, metrics.hallucinated) == (3, 2, 1)


def test_citation_metrics_deduplicate_and_handle_empty_sets() -> None:
    metrics = citation_metrics(cited=["S1", "S1"], offered={"S1"}, relevant_offered={"S1"})
    assert metrics.precision == pytest.approx(1.0)
    assert metrics.cited == 1

    empty_cited = citation_metrics(cited=[], offered={"S1"}, relevant_offered={"S1"})
    assert empty_cited.precision == 0.0
    assert empty_cited.recall == 0.0
    assert empty_cited.hallucination_rate == 0.0

    no_relevant = citation_metrics(cited=["S1"], offered={"S1"}, relevant_offered=set())
    assert no_relevant.precision == pytest.approx(1.0)
    assert no_relevant.recall == 0.0


def test_citation_metrics_from_answer_uses_the_production_parser() -> None:
    metrics = citation_metrics_from_answer(
        "Supported by [S1, S9].",
        offered={"S1"},
        relevant_offered={"S1"},
    )
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.hallucination_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# system metrics
# ---------------------------------------------------------------------------
def test_percentile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 50) == 2.0  # position ceil(0.5 * 4) = 2
    assert percentile(values, 95) == 4.0  # position ceil(3.8) = 4
    assert percentile(values, 99) == 4.0
    assert percentile(values, 0) == 1.0
    assert percentile(values, 100) == 4.0
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 50) == 7.0


def test_latency_percentiles_flattens_stages_and_omits_empty_ones() -> None:
    metrics = latency_percentiles({"fusion": [1.0, 2.0, 3.0, 4.0], "rerank": []})
    assert metrics == {"fusion_p50_ms": 2.0, "fusion_p95_ms": 4.0, "fusion_p99_ms": 4.0}


def test_token_summary_reports_priced_coverage() -> None:
    priced = token_summary(
        [TokenUsage(model="gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)]
    )
    assert priced.calls == 1
    assert priced.prompt_tokens == 1000
    assert priced.completion_tokens == 500
    assert priced.total_tokens == 1500
    assert priced.priced_fraction == pytest.approx(1.0)
    assert priced.cost_usd > 0.0
    assert priced.cost_is_partial is False
    assert priced.fallback_rate == 0.0


def test_token_summary_marks_unpriced_calls_as_partial() -> None:
    summary = token_summary(
        [
            TokenUsage(model="gpt-4o-mini", prompt_tokens=10, completion_tokens=5),
            TokenUsage(model="totally-unknown-model", prompt_tokens=10, completion_tokens=5),
        ]
    )
    assert summary.calls == 2
    assert summary.priced_calls == 1
    assert summary.priced_fraction == pytest.approx(0.5)
    assert summary.cost_is_partial is True


def test_rates_and_histogram() -> None:
    assert fallback_rate([True, False, True]) == pytest.approx(2 / 3)
    assert fallback_rate([]) == 0.0
    assert error_rate(1, 4) == pytest.approx(0.25)
    assert error_rate(1, 0) == 0.0
    assert rate(0, 0) == 0.0
    assert degradation_histogram(["b", "a", "b"]) == {"a": 1, "b": 2}
    assert degradation_histogram([]) == {}
