"""Rubric scoring with a scripted gateway and no database.

The two load-bearing assertions are that the recorded total is computed from the
criterion weights in Python (a model's own arithmetic is ignored) and that a
misconception with no supporting citation is never asserted.
"""

from __future__ import annotations

import uuid

import pytest

from coursellm.assessment.evaluator import (
    compute_total,
    ground_misconceptions,
    score_item,
    score_multiple_choice,
)
from coursellm.assessment.schemas import (
    INCOMPLETE_RUBRIC,
    UNGROUNDED_MISCONCEPTION_DROPPED,
    GeneratedCriterion,
    GeneratedMisconception,
    GeneratedScore,
    QuizItem,
    RubricCriterion,
    default_rubric,
)
from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.llm import LLMRequest, LLMResponse

pytestmark = pytest.mark.unit

CONCEPT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _response(parsed: GeneratedScore) -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        parsed=parsed,
        model="scripted",
        provider="test",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost_usd=0.0,
        latency_ms=0.0,
        cached=False,
        fallback_used=False,
        finish_reason="stop",
    )


class ScriptedGateway:
    def __init__(self, payload: GeneratedScore | None = None, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self.fail:
            raise ServiceUnavailableError("scripted provider outage")
        assert self.payload is not None
        return _response(self.payload)

    async def stream(self, request: LLMRequest):  # pragma: no cover - not used
        yield ""


def _item() -> QuizItem:
    return QuizItem(
        item_id="item-1",
        item_type="short_answer",
        prompt="Explain attention.",
        model_answer="Attention weights the inputs.",
        rubric=default_rubric(),
        citation_ids=["S1"],
        concept_id=CONCEPT_ID,
    )


def _criteria(*scores: float) -> list[GeneratedCriterion]:
    names = [criterion.criterion for criterion in default_rubric()]
    return [
        GeneratedCriterion(criterion=name, score=score, justification="because")
        for name, score in zip(names, scores, strict=False)
    ]


async def test_total_is_computed_from_weights_and_overrides_model_total(
    settings: Settings,
) -> None:
    # correctness 1.0 (0.5), reasoning 0.5 (0.3), grounding 0.0 (0.2) -> 0.65.
    payload = GeneratedScore(criteria=_criteria(1.0, 0.5, 0.0), total=0.1)
    gateway = ScriptedGateway(payload)

    outcome = await score_item(
        _item(),
        "attention is all you need",
        settings=settings,
        gateway=gateway,
        available_citations={"S1"},
    )

    assert outcome.score == pytest.approx(0.65)
    assert outcome.score != payload.total
    assert [score.criterion for score in outcome.rubric] == [
        "correctness",
        "reasoning",
        "grounding",
    ]


async def test_a_rubric_with_a_missing_criterion_is_rejected(settings: Settings) -> None:
    payload = GeneratedScore(criteria=_criteria(1.0, 1.0))
    gateway = ScriptedGateway(payload)

    outcome = await score_item(
        _item(),
        "attention",
        settings=settings,
        gateway=gateway,
        available_citations={"S1"},
    )

    assert outcome.score is None
    assert outcome.degraded == [INCOMPLETE_RUBRIC]


async def test_a_rubric_with_an_invented_criterion_is_rejected(settings: Settings) -> None:
    criteria = _criteria(1.0, 1.0, 1.0)
    criteria.append(GeneratedCriterion(criterion="style", score=1.0))
    gateway = ScriptedGateway(GeneratedScore(criteria=criteria))

    outcome = await score_item(
        _item(),
        "attention",
        settings=settings,
        gateway=gateway,
        available_citations={"S1"},
    )

    assert outcome.score is None
    assert INCOMPLETE_RUBRIC in outcome.degraded


async def test_an_ungrounded_misconception_is_dropped(settings: Settings) -> None:
    payload = GeneratedScore(
        criteria=_criteria(0.0, 0.0, 0.0),
        misconceptions=[
            GeneratedMisconception(
                misconception_type="confusion",
                description="Wrong.",
                corrected_statement="Right.",
                severity="high",
                citation_ids=["S9"],
            )
        ],
    )
    gateway = ScriptedGateway(payload)

    outcome = await score_item(
        _item(),
        "wrong",
        settings=settings,
        gateway=gateway,
        available_citations={"S1"},
    )

    assert outcome.misconceptions == []
    assert UNGROUNDED_MISCONCEPTION_DROPPED in outcome.degraded


async def test_a_grounded_misconception_is_kept_with_its_citation(
    settings: Settings,
) -> None:
    payload = GeneratedScore(
        criteria=_criteria(0.0, 0.0, 0.0),
        misconceptions=[
            GeneratedMisconception(
                misconception_type="confusion",
                description="Wrong.",
                corrected_statement="Right.",
                severity="high",
                citation_ids=["S9", "S1"],
            )
        ],
    )
    gateway = ScriptedGateway(payload)

    outcome = await score_item(
        _item(),
        "wrong",
        settings=settings,
        gateway=gateway,
        available_citations={"S1"},
    )

    assert len(outcome.misconceptions) == 1
    assert outcome.misconceptions[0].citation_ids == ["S1"]
    assert UNGROUNDED_MISCONCEPTION_DROPPED not in outcome.degraded


def test_compute_total_clips_scores_to_the_unit_interval() -> None:
    assert compute_total([(1.0, 1.5)]) == 1.0
    assert compute_total([(1.0, -0.5)]) == 0.0
    assert compute_total([(0.5, 1.0), (0.5, 0.0)]) == pytest.approx(0.5)


def test_compute_total_handles_zero_weight() -> None:
    assert compute_total([(0.0, 1.0)]) == 0.0
    assert compute_total([]) == 0.0


def test_compute_total_is_weighted_not_a_plain_mean() -> None:
    # 0.5 * 1.0 + 0.3 * 0.0 + 0.2 * 0.0 == 0.5, not 1/3.
    assert compute_total([(0.5, 1.0), (0.3, 0.0), (0.2, 0.0)]) == pytest.approx(0.5)


async def test_scoring_is_deterministic(settings: Settings) -> None:
    payload = GeneratedScore(criteria=_criteria(0.4, 0.6, 0.8))
    first = await score_item(
        _item(),
        "attention",
        settings=settings,
        gateway=ScriptedGateway(payload),
        available_citations={"S1"},
    )
    second = await score_item(
        _item(),
        "attention",
        settings=settings,
        gateway=ScriptedGateway(payload),
        available_citations={"S1"},
    )

    assert first.score == second.score
    assert first.rubric == second.rubric


def test_score_multiple_choice_accepts_index_letter_and_text() -> None:
    item = QuizItem(
        item_id="item-1",
        item_type="multiple_choice",
        prompt="Which?",
        choices=["alpha", "beta", "gamma", "delta"],
        correct_choice_index=1,
        rubric=[RubricCriterion(criterion="correct_option", weight=1.0)],
        citation_ids=["S1"],
    )

    assert score_multiple_choice(item, "1") == 1.0
    assert score_multiple_choice(item, "B") == 1.0
    assert score_multiple_choice(item, "beta") == 1.0
    assert score_multiple_choice(item, "0") == 0.0
    assert score_multiple_choice(item, "gamma") == 0.0
    assert score_multiple_choice(item, "unparseable") == 0.0


def test_ground_misconceptions_is_a_pure_filter() -> None:
    kept, dropped = ground_misconceptions(
        [
            GeneratedMisconception(
                misconception_type="a",
                description="d",
                corrected_statement="c",
                citation_ids=["S1"],
            ),
            GeneratedMisconception(
                misconception_type="b",
                description="d",
                corrected_statement="c",
                citation_ids=[],
            ),
        ],
        {"S1"},
    )

    assert [item.misconception_type for item in kept] == ["a"]
    assert dropped is True
