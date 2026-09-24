"""Native evaluation metrics for the CourseLLM retrieval and generation halves.

The retrieval, citation and system modules are pure and need no model. The
generation module needs a :class:`~evals.judges.llm_judge.Judge`; without a real
one its metrics are reported as ``not_measured`` rather than estimated.
"""

from __future__ import annotations

from evals.metrics.citations import (
    CitationMetrics,
    citation_metrics,
    citation_metrics_from_answer,
)
from evals.metrics.generation import (
    Measured,
    MetricOutcome,
    NotMeasured,
    answer_correctness,
    answer_relevance,
    correctness_from_verdict,
    faithfulness,
    faithfulness_from_verdict,
    measure,
    relevance_from_verdict,
)
from evals.metrics.retrieval import (
    context_precision,
    context_recall,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evals.metrics.system import (
    PERCENTILES,
    TokenSummary,
    TokenUsage,
    degradation_histogram,
    error_rate,
    fallback_rate,
    latency_percentiles,
    percentile,
    rate,
    token_summary,
)

__all__ = [
    "PERCENTILES",
    "CitationMetrics",
    "Measured",
    "MetricOutcome",
    "NotMeasured",
    "TokenSummary",
    "TokenUsage",
    "answer_correctness",
    "answer_relevance",
    "citation_metrics",
    "citation_metrics_from_answer",
    "context_precision",
    "context_recall",
    "correctness_from_verdict",
    "degradation_histogram",
    "error_rate",
    "faithfulness",
    "faithfulness_from_verdict",
    "fallback_rate",
    "latency_percentiles",
    "measure",
    "mrr",
    "ndcg_at_k",
    "percentile",
    "precision_at_k",
    "rate",
    "recall_at_k",
    "relevance_from_verdict",
    "token_summary",
]
