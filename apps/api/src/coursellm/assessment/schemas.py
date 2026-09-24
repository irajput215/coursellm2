"""Typed domain objects for assessment: quiz items and scored answers.

These are the shapes the assessment layer passes between its pure functions and
its persistence layer. They are deliberately separate from both the gateway's
response schemas (which describe what a *model* may return) and the ORM rows, so
that generation and scoring can be exercised without a database and a model
cannot smuggle an invalid item past the boundary.

Three invariants are stated here rather than enforced by prose.

* **A quiz item is either grounded or absent.** ``citation_ids`` is non-empty on
  every item that survives generation; an item with no resolvable citation is
  dropped by the generator, never padded.
* **A multiple-choice item has exactly four options and exactly one correct
  index.** The gateway's schema carries a list (``correct_choice_indices``) so
  that a model returning two correct options is a value the code can *reject*
  rather than a value it silently loses.
* **Misconceptions are typed and evidentiary.** A misconception carries the
  citation ids of the passages that contradict it; the evaluator drops any that
  cite nothing it was shown.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ItemType = Literal["multiple_choice", "short_answer", "concept_check"]
Difficulty = Literal["easy", "medium", "hard"]
Severity = Literal["low", "medium", "high"]

#: The canonical rubric used for free-text items. Named criteria with weights
#: that sum to 1.0; the total is always computed from these weights in Python.
#: The model is asked for a score per criterion and never for a total.
DEFAULT_RUBRIC: tuple[tuple[str, float], ...] = (
    ("correctness", 0.5),
    ("reasoning", 0.3),
    ("grounding", 0.2),
)

#: The single criterion a multiple-choice item is scored against. A multiple
#: choice item is graded deterministically, without a model call.
MULTIPLE_CHOICE_CRITERION = "correct_option"
#: Score below which an evaluation writes ``concept_struggled`` rather than
#: ``quiz_attempt``. Documented here because the projection's clients depend on
#: the meaning of the kind, not merely its spelling.
STRUGGLE_THRESHOLD = 0.5

#: Retrieval returned no passage at all.
NO_EVIDENCE = "no_evidence"
#: The gateway could not complete the structured call.
LLM_UNAVAILABLE = "llm_unavailable"
#: The gateway completed but returned nothing that validates against the schema.
STRUCTURED_OUTPUT_FAILED = "structured_output_failed"
#: The model returned no item that resolved to a retrieved passage.
NO_GROUNDED_ITEMS = "no_grounded_items"
#: Fewer grounded items than requested; the shortfall is reported, not padded.
INSUFFICIENT_GROUNDED_ITEMS = "insufficient_grounded_items"
#: The response left out one or more of the item's rubric criteria.
INCOMPLETE_RUBRIC = "incomplete_rubric"
#: A misconception cited evidence the student was never shown, so it was dropped.
UNGROUNDED_MISCONCEPTION_DROPPED = "ungrounded_misconception_dropped"


class RubricCriterion(BaseModel):
    """One named, weighted criterion. No holistic score exists outside a rubric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion: str = Field(min_length=1, max_length=120)
    weight: float = Field(gt=0.0, le=1.0)
    description: str = Field(default="", max_length=500)


class RubricScore(BaseModel):
    """The model's score and justification for one criterion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion: str
    weight: float
    score: float = Field(ge=0.0, le=1.0)
    justification: str = Field(default="", max_length=1000)


class Misconception(BaseModel):
    """A typed, evidenced misconception found in an answer.

    ``citation_ids`` are the passages that contradict the answer. The evaluator
    intersects them with the evidence the item actually carried, so a
    misconception with no surviving citation is dropped rather than asserted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    misconception_type: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    corrected_statement: str = Field(min_length=1, max_length=1000)
    severity: Severity = "medium"
    citation_ids: list[str] = Field(default_factory=list)
    confidence: Severity = "medium"


class QuizItem(BaseModel):
    """One generated quiz item, grounded in the cited passages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=120)
    item_type: ItemType
    prompt: str = Field(min_length=1, max_length=4000)
    choices: list[str] = Field(default_factory=list)
    correct_choice_index: int | None = None
    model_answer: str | None = None
    correct_boolean: bool | None = None
    rubric: list[RubricCriterion] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    concept_id: uuid.UUID | None = None
    justification: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> QuizItem:
        """Reject an item whose type and payload disagree.

        The check is structural, not cosmetic: a multiple-choice item with the
        wrong number of options cannot be graded, and a short-answer item with
        no model answer cannot be scored, so accepting either would only move
        the failure to grading time.
        """
        if self.item_type == "multiple_choice":
            if len(self.choices) != 4:
                msg = "A multiple_choice item must have exactly four choices."
                raise ValueError(msg)
            if self.correct_choice_index is None or not (0 <= self.correct_choice_index < 4):
                msg = "A multiple_choice item must have exactly one correct option."
                raise ValueError(msg)
        elif self.item_type == "short_answer":
            if not self.model_answer:
                msg = "A short_answer item must carry a model answer."
                raise ValueError(msg)
        elif self.correct_boolean is None:
            msg = "A concept_check item must carry the correct boolean value."
            raise ValueError(msg)
        if not self.citation_ids:
            msg = "A quiz item must cite at least one supporting passage."
            raise ValueError(msg)
        return self


class QuizCitation(BaseModel):
    """A citation attached to a persisted draft.

    ``quote`` is the supporting passage text, kept so that grading can show the
    model the evidence the item was grounded in without re-running retrieval.
    It is bounded by the generator, and like every citation quote it is data,
    never instruction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None = None
    source_type: str
    quote: str = ""


class QuizDraft(BaseModel):
    """A generated, persisted quiz draft, or a typed failure.

    ``shortfall`` is the number of requested items that were not produced because
    no evidence supported them. It is reported explicitly so a caller can tell a
    three-item quiz that was asked for as three from one that was asked for as
    five and honestly delivered less.
    """

    model_config = ConfigDict(extra="forbid")

    quiz_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    concept_ids: list[uuid.UUID] = Field(default_factory=list)
    difficulty: Difficulty = "medium"
    item_types: list[ItemType] = Field(default_factory=list)
    items: list[QuizItem] = Field(default_factory=list)
    citations: list[QuizCitation] = Field(default_factory=list)
    requested_items: int = 0
    shortfall: int = 0
    degraded: list[str] = Field(default_factory=list)


class AssessmentResult(BaseModel):
    """A scored answer with the mastery delta it implies.

    ``score`` is ``None`` only for a typed failure (the rubric was incomplete or
    the gateway failed); a scored answer always has a number computed by
    :mod:`coursellm.assessment.evaluator`, never by the model.
    """

    model_config = ConfigDict(extra="forbid")

    quiz_attempt_id: uuid.UUID
    item_id: str
    quiz_id: uuid.UUID | None = None
    score: float | None = None
    rubric: list[RubricScore] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    mastery_delta: float = 0.0
    progress_event_kind: str | None = None
    degraded: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gateway response schemas
#
# These describe what a model is permitted to return. They are permissive where
# the domain model above is strict, because the generator and evaluator must be
# able to *see* an invalid value (two correct options, a missing criterion) and
# reject it with a reason rather than have the gateway raise a validation error
# that carries no domain meaning.
# ---------------------------------------------------------------------------
class GeneratedQuizItem(BaseModel):
    """One item as returned by the quiz-generation model."""

    model_config = ConfigDict(extra="ignore")

    item_type: ItemType
    prompt: str = ""
    choices: list[str] = Field(default_factory=list)
    correct_choice_indices: list[int] = Field(default_factory=list)
    model_answer: str | None = None
    correct_boolean: bool | None = None
    rubric: list[RubricCriterion] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    concept_id: str | None = None
    justification: str | None = None


class GeneratedQuiz(BaseModel):
    """The structured-output schema for quiz generation."""

    model_config = ConfigDict(extra="ignore")

    items: list[GeneratedQuizItem] = Field(default_factory=list)


class GeneratedCriterion(BaseModel):
    """One per-criterion score as returned by the evaluation model."""

    model_config = ConfigDict(extra="ignore")

    criterion: str
    score: float
    justification: str = ""


class GeneratedMisconception(BaseModel):
    """One misconception as returned by the evaluation model."""

    model_config = ConfigDict(extra="ignore")

    misconception_type: str
    description: str
    corrected_statement: str
    severity: Severity = "medium"
    citation_ids: list[str] = Field(default_factory=list)


class GeneratedScore(BaseModel):
    """The structured-output schema for answer evaluation.

    ``total`` is accepted and deliberately ignored: the documented rule is that
    the total is computed from the weights in Python, so a model that returns
    arithmetic inconsistent with its own criterion scores cannot change the
    recorded grade.
    """

    model_config = ConfigDict(extra="ignore")

    criteria: list[GeneratedCriterion] = Field(default_factory=list)
    misconceptions: list[GeneratedMisconception] = Field(default_factory=list)
    total: float | None = None


__all__ = [
    "DEFAULT_RUBRIC",
    "INCOMPLETE_RUBRIC",
    "INSUFFICIENT_GROUNDED_ITEMS",
    "LLM_UNAVAILABLE",
    "MULTIPLE_CHOICE_CRITERION",
    "NO_EVIDENCE",
    "NO_GROUNDED_ITEMS",
    "STRUCTURED_OUTPUT_FAILED",
    "STRUGGLE_THRESHOLD",
    "UNGROUNDED_MISCONCEPTION_DROPPED",
    "AssessmentResult",
    "Difficulty",
    "GeneratedCriterion",
    "GeneratedMisconception",
    "GeneratedQuiz",
    "GeneratedQuizItem",
    "GeneratedScore",
    "ItemType",
    "Misconception",
    "QuizCitation",
    "QuizDraft",
    "QuizItem",
    "RubricCriterion",
    "RubricScore",
    "Severity",
    "default_rubric",
]


def default_rubric() -> list[RubricCriterion]:
    """The canonical free-text rubric, as fresh models."""
    return [RubricCriterion(criterion=name, weight=weight) for name, weight in DEFAULT_RUBRIC]
