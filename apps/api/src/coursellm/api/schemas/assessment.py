"""Request and response schemas for quizzes and the progress read model.

**What is deliberately absent.** A quiz response carries the items, their
citations, the rubric and the shortfall, but not the model prompt or the raw
evidence quotes that produced an item. The citation ids resolve to a document
and a page, which is what a reader can check; the retrieved passage text stays in
the persisted draft where grading needs it and nowhere else.

The correct answer is part of the item rather than a separate "grade" response,
because the item is generated once and served from storage afterwards: splitting
the two would mean the stored draft and the served draft could disagree about
what was asked.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.api.schemas.roadmap import NextActionResponse, RoadmapPositionResponse
from coursellm.assessment.schemas import (
    AssessmentResult,
    Difficulty,
    ItemType,
    QuizDraft,
)

Severity = Literal["low", "medium", "high"]


def _default_item_types() -> list[ItemType]:
    return ["multiple_choice"]


class QuizCreateRequest(BaseModel):
    """Generate a grounded quiz for one of the caller's courses."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    concept_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=50,
        description="Optional concepts to align the quiz to; ids that do not resolve are ignored.",
    )
    n_items: int = Field(default=5, ge=1, le=20)
    difficulty: Difficulty = "medium"
    item_types: list[ItemType] = Field(
        default_factory=_default_item_types,
        max_length=3,
        description="Item kinds to generate. Unsupported kinds are dropped by the generator.",
    )


class AnswerSubmitRequest(BaseModel):
    """One submitted answer, as free text (a choice index, letter or text)."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(max_length=10_000)


class RubricCriterionResponse(BaseModel):
    """One named, weighted criterion of an item's rubric."""

    model_config = ConfigDict(from_attributes=True)

    criterion: str
    weight: float
    description: str = ""


class QuizCitationResponse(BaseModel):
    """A citation attached to a quiz, without the quoted passage text."""

    model_config = ConfigDict(from_attributes=True)

    citation_id: str
    document_id: uuid.UUID
    filename: str
    page: int | None = None
    source_type: str


class QuizItemResponse(BaseModel):
    """One generated item, with its rubric and its supporting citation ids."""

    model_config = ConfigDict(from_attributes=True)

    item_id: str
    item_type: ItemType
    prompt: str
    choices: list[str]
    correct_choice_index: int | None
    model_answer: str | None
    correct_boolean: bool | None
    rubric: list[RubricCriterionResponse]
    citation_ids: list[str]
    concept_id: uuid.UUID | None
    justification: str | None


class QuizResponse(BaseModel):
    """A generated draft, with the shortfall it honestly recorded."""

    model_config = ConfigDict(from_attributes=True)

    quiz_id: uuid.UUID
    course_id: uuid.UUID
    concept_ids: list[uuid.UUID]
    difficulty: Difficulty
    item_types: list[ItemType]
    items: list[QuizItemResponse]
    citations: list[QuizCitationResponse]
    requested_items: int
    shortfall: int
    degraded: list[str]


class RubricScoreResponse(BaseModel):
    """The model's score and justification for one criterion."""

    model_config = ConfigDict(from_attributes=True)

    criterion: str
    weight: float
    score: float
    justification: str = ""


class MisconceptionResponse(BaseModel):
    """A typed misconception with the passages that contradict it."""

    model_config = ConfigDict(from_attributes=True)

    misconception_type: str
    description: str
    corrected_statement: str
    severity: Severity
    citation_ids: list[str]
    confidence: Severity


class AssessmentResultResponse(BaseModel):
    """A scored answer, its mastery delta and the event kind it wrote."""

    model_config = ConfigDict(from_attributes=True)

    quiz_attempt_id: uuid.UUID
    item_id: str
    quiz_id: uuid.UUID | None
    score: float | None
    rubric: list[RubricScoreResponse]
    misconceptions: list[MisconceptionResponse]
    mastery_delta: float
    progress_event_kind: str | None
    degraded: list[str]


class AttemptResponse(BaseModel):
    """One stored attempt, as read back from the append-only log."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quiz_id: uuid.UUID | None
    course_id: uuid.UUID
    item_id: str
    concept_ids: list[uuid.UUID]
    answer: str | None
    score: float
    rubric: list[dict[str, Any]]
    # Typed objects, not JSON-encoded strings: the JSONB column now stores the
    # misconception's fields directly, so a consumer no longer parses twice.
    misconceptions: list[MisconceptionResponse]
    created_at: datetime


class AttemptPageResponse(BaseModel):
    """One page of the caller's attempt history."""

    items: list[AttemptResponse]
    total: int
    limit: int
    offset: int


class ProgressSummaryResponse(BaseModel):
    """Mastery, weak/stale concepts, velocity, recent attempts and the next action."""

    mastery: dict[uuid.UUID, float]
    weak_concepts: list[uuid.UUID]
    stale_concepts: list[uuid.UUID]
    velocity: float | None
    attempt_counts: dict[uuid.UUID, int]
    recent_attempts: list[AttemptResponse]
    current_position: RoadmapPositionResponse | None
    next_action: NextActionResponse | None
    degraded: list[str]


def quiz_response(draft: QuizDraft) -> QuizResponse:
    """Convert a domain draft into its wire representation.

    A draft is only served after it has been persisted, so ``quiz_id`` and
    ``course_id`` are present. The fallback keeps the converter total rather than
    raising on a draft the service never returns.
    """
    if draft.quiz_id is None or draft.course_id is None:  # pragma: no cover - service invariant
        msg = "A quiz response requires a persisted draft."
        raise ValueError(msg)
    return QuizResponse(
        quiz_id=draft.quiz_id,
        course_id=draft.course_id,
        concept_ids=list(draft.concept_ids),
        difficulty=draft.difficulty,
        item_types=list(draft.item_types),
        items=[QuizItemResponse.model_validate(item) for item in draft.items],
        citations=[QuizCitationResponse.model_validate(citation) for citation in draft.citations],
        requested_items=draft.requested_items,
        shortfall=draft.shortfall,
        degraded=list(draft.degraded),
    )


def assessment_response(result: AssessmentResult) -> AssessmentResultResponse:
    """Convert a domain assessment result into its wire representation."""
    return AssessmentResultResponse.model_validate(result)


__all__ = [
    "AnswerSubmitRequest",
    "AssessmentResultResponse",
    "AttemptPageResponse",
    "AttemptResponse",
    "MisconceptionResponse",
    "ProgressSummaryResponse",
    "QuizCitationResponse",
    "QuizCreateRequest",
    "QuizItemResponse",
    "QuizResponse",
    "RubricCriterionResponse",
    "RubricScoreResponse",
    "assessment_response",
    "quiz_response",
]
