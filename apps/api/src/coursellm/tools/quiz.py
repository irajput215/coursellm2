"""``create_quiz`` and ``evaluate_answer`` — the assessment write tools.

Both are typed stubs: quiz and attempt persistence is PR 13. The contract, the
permissions and the ``write`` side-effect class are real, so the permission
matrix, the no-retry-on-write policy and the audit path are exercised now. Each
handler returns a typed empty result with ``degraded=["tool_unavailable"]``
rather than a plausible-looking item: a fabricated quiz item would be graded, and
a fabricated grade is worse than an explicit failure.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

Difficulty = Literal["easy", "medium", "hard"]
ItemType = Literal["multiple_choice", "short_answer", "true_false"]

_TOOL_UNAVAILABLE = ["tool_unavailable"]


def _default_item_types() -> list[ItemType]:
    return ["multiple_choice"]


class QuizItem(BaseModel):
    """One generated quiz item, grounded in cited passages."""

    item_id: str
    prompt: str
    item_type: ItemType
    choices: list[str] = Field(default_factory=list)
    answer: str | None = None
    rubric: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)


class CreateQuizArgs(BaseModel):
    """Arguments for ``create_quiz``. ``tenant_id`` is injected, not accepted."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    concept_ids: list[str] = Field(default_factory=list, max_length=50)
    n_items: int = Field(default=5, ge=1, le=20)
    difficulty: Difficulty = "medium"
    item_types: list[ItemType] = Field(default_factory=_default_item_types)


class QuizDraft(BaseModel):
    """A persisted quiz draft, or an empty one with a degradation reason."""

    quiz_id: str | None = None
    course_id: str
    items: list[QuizItem] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


class EvaluateAnswerArgs(BaseModel):
    """Arguments for ``evaluate_answer``."""

    model_config = ConfigDict(extra="forbid")

    quiz_attempt_id: uuid.UUID
    item_id: str = Field(min_length=1, max_length=120)
    answer: str = Field(max_length=10_000)


class RubricCriterion(BaseModel):
    """One scored rubric line. No holistic score exists outside this list."""

    criterion: str
    met: bool
    weight: float = Field(ge=0.0, le=1.0)


class AssessmentResult(BaseModel):
    """A scored answer with the mastery delta it implies."""

    quiz_attempt_id: str
    item_id: str
    score: float | None = None
    rubric: list[RubricCriterion] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    mastery_delta: float = 0.0
    degraded: list[str] = Field(default_factory=list)


async def create_quiz(args: CreateQuizArgs, ctx: ToolContext) -> QuizDraft:
    """Return an empty quiz draft; persistence lands in PR 13."""
    return QuizDraft(
        quiz_id=None,
        course_id=str(args.course_id),
        items=[],
        degraded=list(_TOOL_UNAVAILABLE),
    )


async def evaluate_answer(args: EvaluateAnswerArgs, ctx: ToolContext) -> AssessmentResult:
    """Return an unscored result; rubric and progress persistence land in PR 13."""
    return AssessmentResult(
        quiz_attempt_id=str(args.quiz_attempt_id),
        item_id=args.item_id,
        score=None,
        rubric=[],
        misconceptions=[],
        mastery_delta=0.0,
        degraded=list(_TOOL_UNAVAILABLE),
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="create_quiz",
            description=(
                "Persist a quiz draft aligned to concepts and grounded in retrieved "
                "passages, with per-item citations and a rubric."
            ),
            parameters=CreateQuizArgs,
            required_permissions=frozenset({Permission.QUIZ_WRITE, Permission.DOCUMENTS_READ}),
            side_effects="write",
            timeout_ms=15_000,
            handler=create_quiz,
        )
    )
    registry.register(
        ToolSpec(
            name="evaluate_answer",
            description=(
                "Score a submitted answer against the item's rubric and record the "
                "mastery delta and misconception tags."
            ),
            parameters=EvaluateAnswerArgs,
            required_permissions=frozenset({Permission.QUIZ_WRITE, Permission.PROGRESS_WRITE}),
            side_effects="write",
            timeout_ms=12_000,
            handler=evaluate_answer,
        )
    )


__all__ = [
    "AssessmentResult",
    "CreateQuizArgs",
    "EvaluateAnswerArgs",
    "QuizDraft",
    "QuizItem",
    "RubricCriterion",
    "create_quiz",
    "evaluate_answer",
    "register",
]
