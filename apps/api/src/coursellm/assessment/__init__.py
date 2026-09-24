"""Assessment and progress: grounded quiz generation and rubric scoring.

The package splits along one line: :mod:`coursellm.assessment.generator` and
:mod:`coursellm.assessment.evaluator` own the model boundary and the validation
that surrounds it, while :mod:`coursellm.assessment.service` owns persistence and
the use cases. That split is what lets the two hard parts — an item that must be
grounded and a grade that must be computed from weights, not from a model's
arithmetic — be tested with a scripted gateway and no database.
"""

from __future__ import annotations

from coursellm.assessment.generator import draft_from_context, generate_quiz
from coursellm.assessment.schemas import (
    DEFAULT_RUBRIC,
    STRUGGLE_THRESHOLD,
    AssessmentResult,
    Misconception,
    QuizCitation,
    QuizDraft,
    QuizItem,
    RubricCriterion,
    RubricScore,
)
from coursellm.assessment.service import AssessmentService, AttemptPage

__all__ = [
    "DEFAULT_RUBRIC",
    "STRUGGLE_THRESHOLD",
    "AssessmentResult",
    "AssessmentService",
    "AttemptPage",
    "Misconception",
    "QuizCitation",
    "QuizDraft",
    "QuizItem",
    "RubricCriterion",
    "RubricScore",
    "draft_from_context",
    "generate_quiz",
]
