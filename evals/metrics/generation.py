"""Generation metrics computed from a judge's typed verdicts.

Three metrics, each of which requires a judge:

* **faithfulness** — the fraction of the answer's atomic claims that the
  retrieved context supports.
* **answer relevance** — how directly the answer addresses the question.
* **answer correctness** — how well the answer agrees with the expected answer.

The arithmetic lives here, separate from the model call, so it is testable with
a scripted judge and hand-computed values. A judge that fails or returns an
unparseable verdict raises :class:`~evals.judges.llm_judge.JudgeError`; the
metric functions do not catch it. Use :func:`measure` when the caller wants a
failure recorded as ``not_measured`` rather than propagated.

Normalisation is explicit: relevance and correctness verdicts are graded 1..5
and are mapped to ``[0, 1]`` with ``(score - 1) / 4``. A judge that returns the
middle grade therefore scores ``0.5``, not ``0.6`` or ``3``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evals.judges.llm_judge import (
    JUDGE_FAILURE_REASON,
    AnswerCorrectnessVerdict,
    AnswerRelevanceVerdict,
    FaithfulnessVerdict,
    Judge,
    JudgeError,
)


@dataclass(frozen=True, slots=True)
class Measured:
    """A metric that was computed by a real judge."""

    value: float


@dataclass(frozen=True, slots=True)
class NotMeasured:
    """A metric that could not be measured, with a stable reason code."""

    reason: str


MetricOutcome = Measured | NotMeasured

MetricFn = Callable[..., Awaitable[float]]


def faithfulness_from_verdict(verdict: FaithfulnessVerdict) -> float:
    """``supported claims / total claims``, or ``0.0`` when there are no claims.

    An answer with no extractable claims cannot be shown to be faithful;
    scoring it as ``1.0`` would reward an evasive answer. A refusal is measured
    by the citation and refusal paths instead.
    """
    total = len(verdict.claims)
    if total == 0:
        return 0.0
    supported = sum(1 for claim in verdict.claims if claim.supported)
    return supported / total


def relevance_from_verdict(verdict: AnswerRelevanceVerdict) -> float:
    """Map the 1..5 relevance grade onto ``[0, 1]`` with ``(score - 1) / 4``."""
    return (verdict.score - 1) / 4.0


def correctness_from_verdict(verdict: AnswerCorrectnessVerdict) -> float:
    """Map the 1..5 correctness grade onto ``[0, 1]`` with ``(score - 1) / 4``."""
    return (verdict.score - 1) / 4.0


async def faithfulness(
    judge: Judge, *, question: str, answer: str, contexts: Sequence[str]
) -> float:
    """Fraction of the answer's atomic claims supported by the context."""
    verdict = await judge.faithfulness(question=question, answer=answer, contexts=contexts)
    return faithfulness_from_verdict(verdict)


async def answer_relevance(judge: Judge, *, question: str, answer: str) -> float:
    """Normalised judgement of whether the answer addresses the question."""
    verdict = await judge.answer_relevance(question=question, answer=answer)
    return relevance_from_verdict(verdict)


async def answer_correctness(
    judge: Judge, *, question: str, answer: str, expected_answer: str
) -> float:
    """Normalised agreement between the answer and the expected answer."""
    verdict = await judge.answer_correctness(
        question=question, answer=answer, expected_answer=expected_answer
    )
    return correctness_from_verdict(verdict)


async def measure(metric: MetricFn, /, *args: Any, **kwargs: Any) -> MetricOutcome:
    """Run ``metric`` and record a judge failure as ``not_measured``.

    Only :class:`JudgeError` is converted. Any other exception is a harness bug
    and is allowed to propagate, so it fails the run instead of being hidden as
    a missing metric.
    """
    try:
        return Measured(value=await metric(*args, **kwargs))
    except JudgeError:
        return NotMeasured(reason=JUDGE_FAILURE_REASON)


__all__ = [
    "Measured",
    "MetricOutcome",
    "NotMeasured",
    "answer_correctness",
    "answer_relevance",
    "correctness_from_verdict",
    "faithfulness",
    "faithfulness_from_verdict",
    "measure",
    "relevance_from_verdict",
]
