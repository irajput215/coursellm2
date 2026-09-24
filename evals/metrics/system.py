"""System metrics: latency percentiles, tokens, cost and rates.

These are the numbers that are easiest to get subtly wrong and hardest to
notice, so the definitions are explicit.

**Percentiles use the nearest-rank method.** For ``n`` values sorted ascending,
the ``p``-th percentile is the value at 1-based position ``ceil(p / 100 * n)``,
clamped to ``[1, n]``. No interpolation is performed. The docstring on
:func:`percentile` repeats this because the choice changes the answer: for
``[1, 2, 3, 4]`` the nearest-rank p50 is ``2``, while linear interpolation gives
``2.5``. Every latency number in a report is produced by this one function.

**Cost is reported with its coverage.** A call whose model cannot be priced
contributes ``0.0`` and marks the summary as partially priced, because a total
that silently omits unpriced calls understates spend. ``priced_fraction`` is
reported next to the total so the total can be labelled partial instead of
wrong.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from coursellm.llm.cost import estimate_cost_breakdown

#: Reported for every stage. p99 on a small sample is the maximum by
#: construction with nearest-rank, which is stated rather than hidden.
PERCENTILES: tuple[int, ...] = (50, 95, 99)


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile of ``values``.

    Sorts a copy ascending, then returns the value at 1-based position
    ``ceil(p / 100 * n)`` clamped to ``[1, n]``. An empty sequence returns
    ``0.0``. ``p`` outside ``[0, 100]`` is clamped. This is the only percentile
    implementation in the evaluation harness.
    """
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    bounded = min(max(p, 0.0), 100.0)
    position = math.ceil(bounded / 100.0 * len(ordered))
    index = min(max(position, 1), len(ordered)) - 1
    return ordered[index]


def latency_percentiles(
    stage_timings: Mapping[str, Sequence[float]],
    *,
    percentiles: Sequence[int] = PERCENTILES,
) -> dict[str, float]:
    """Flatten per-stage timings into ``<stage>_p<p>_ms`` metrics.

    A stage with no samples is omitted rather than reported as ``0.0``, because
    "no data" and "instant" are different claims.
    """
    metrics: dict[str, float] = {}
    for stage, timings in stage_timings.items():
        if not timings:
            continue
        for p in percentiles:
            metrics[f"{stage}_p{p}_ms"] = round(percentile(timings, p), 6)
    return metrics


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """One model call's token and model accounting."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class TokenSummary:
    """Aggregate token and cost figures with their coverage."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    priced_calls: int
    priced_fraction: float
    fallback_calls: int
    fallback_rate: float
    cost_is_partial: bool = field(default=False)


def token_summary(usages: Sequence[TokenUsage]) -> TokenSummary:
    """Sum tokens and estimated cost, and report how much could be priced.

    ``priced_fraction`` is ``priced_calls / calls`` (``0.0`` when there were no
    calls). ``cost_is_partial`` is true when at least one call could not be
    priced, so a reader is never shown an unlabelled partial total.
    """
    calls = len(usages)
    prompt = sum(max(usage.prompt_tokens, 0) for usage in usages)
    completion = sum(max(usage.completion_tokens, 0) for usage in usages)
    cost = 0.0
    priced = 0
    for usage in usages:
        estimate = estimate_cost_breakdown(
            usage.model, usage.prompt_tokens, usage.completion_tokens
        )
        if estimate.priced:
            priced += 1
        cost += estimate.cost_usd
    fallbacks = sum(1 for usage in usages if usage.fallback_used)
    return TokenSummary(
        calls=calls,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cost_usd=round(cost, 8),
        priced_calls=priced,
        priced_fraction=(priced / calls) if calls else 0.0,
        fallback_calls=fallbacks,
        fallback_rate=(fallbacks / calls) if calls else 0.0,
        cost_is_partial=priced < calls,
    )


def rate(numerator: int, denominator: int) -> float:
    """``numerator / denominator`` with ``0.0`` for an empty denominator."""
    return (numerator / denominator) if denominator else 0.0


def fallback_rate(flags: Sequence[bool]) -> float:
    """Fraction of calls that were answered by a fallback model."""
    return rate(sum(1 for flag in flags if flag), len(flags))


def degradation_histogram(reasons: Sequence[str]) -> dict[str, int]:
    """Count each machine-readable degradation reason, deterministically ordered."""
    return dict(sorted(Counter(reasons).items()))


def error_rate(errors: int, total: int) -> float:
    """Fraction of attempts that raised or were recorded as errors."""
    return rate(errors, total)


__all__ = [
    "PERCENTILES",
    "TokenSummary",
    "TokenUsage",
    "degradation_histogram",
    "error_rate",
    "fallback_rate",
    "latency_percentiles",
    "percentile",
    "rate",
    "token_summary",
]
