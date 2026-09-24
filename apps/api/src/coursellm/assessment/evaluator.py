"""Rubric scoring and misconception detection.

Three rules carry this module, and each closes a way a model could produce a
grade the evidence does not support.

* **The total is computed in Python from the weights.** The model returns a score
  and a justification per criterion; it is never asked for, and never trusted
  with, the arithmetic. A response whose own ``total`` disagrees with its
  per-criterion scores therefore cannot change the recorded grade.
* **A rubric must be answered completely.** A response that omits a criterion
  (or invents one) is a typed failure with no score, not a partial grade scaled
  to whatever the model happened to return.
* **A misconception must be evidenced.** A reported misconception is kept only
  when at least one of its citation ids resolves to a passage the item actually
  carried; otherwise it is dropped. The rest of the record is typed:
  ``misconception_type``, ``description``, ``corrected_statement`` and
  ``severity``.

Scoring itself is deterministic: a multiple-choice item is graded in Python with
no model call, and every other item is graded against the fixed rubric attached
to the item at generation time.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.assessment.schemas import (
    INCOMPLETE_RUBRIC,
    LLM_UNAVAILABLE,
    STRUCTURED_OUTPUT_FAILED,
    STRUGGLE_THRESHOLD,
    UNGROUNDED_MISCONCEPTION_DROPPED,
    AssessmentResult,
    GeneratedCriterion,
    GeneratedMisconception,
    GeneratedScore,
    Misconception,
    QuizCitation,
    QuizItem,
    RubricCriterion,
    RubricScore,
)
from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError, ServiceUnavailableError, UpstreamError
from coursellm.core.logging import get_logger
from coursellm.db.models.assessment import Quiz
from coursellm.db.models.learning import ProgressEvent, ProgressEventKind, QuizAttempt
from coursellm.db.tenancy import TenantScope
from coursellm.learning.progress import project_mastery
from coursellm.llm import ChatMessage, LLMGateway, LLMRequest, ModelTask
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import EVIDENCE_CLOSE_TAG, EVIDENCE_OPEN_TAG
from coursellm.repositories.content import CourseRepository

logger = get_logger(__name__)

#: The versioned prompt that carries the scoring and misconception rules.
EVALUATE_TEMPLATE = "assessor.evaluate"
#: A recorded grade is never overwritten; a regrade is a new row.
REGRADE = "regrade"


@dataclass(frozen=True, slots=True)
class ScoredOutcome:
    """The result of scoring one answer, before anything is persisted."""

    score: float | None
    rubric: list[RubricScore] = field(default_factory=list)
    misconceptions: list[Misconception] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


async def evaluate_answer(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    gateway: LLMGateway,
    *,
    user_id: uuid.UUID,
    quiz_attempt_id: uuid.UUID,
    item_id: str,
    answer: str,
) -> AssessmentResult:
    """Score one submitted answer, persist the attempt and write one event.

    The attempt must already exist and belong to ``user_id``; a row belonging to
    another tenant is invisible under Row-Level Security, and a row belonging to
    another user in the same tenant is filtered out here, so both are 404s. A
    submission for an attempt that was already graded creates a *new* attempt
    row rather than editing the recorded grade.
    """
    attempt = await _load_attempt(session, scope, user_id=user_id, attempt_id=quiz_attempt_id)
    quiz = await _load_quiz(session, scope, quiz_id=attempt.quiz_id)
    item = _find_item(quiz, item_id)
    if item is None:
        msg = "Quiz item not found."
        raise NotFoundError(msg)

    degraded: list[str] = []
    # ``QuizAttempt.score`` is non-nullable, so a graded row is identified by its
    # rubric rather than by a null score. An already-graded attempt is never
    # edited: a regrade is a new row, and therefore a new event.
    if attempt.rubric:
        attempt = await _clone_attempt(session, scope, attempt)
        degraded.append(REGRADE)
    attempt.answer = answer

    citations = _citation_map(quiz)
    course = await CourseRepository(session, scope).get(attempt.course_id)
    course_name = course.name if course is not None else "your course"
    evidence = render_evidence(item, citations)
    available = {citation_id for citation_id in item.citation_ids if citation_id in citations}

    outcome = await score_item(
        item,
        answer,
        settings=settings,
        gateway=gateway,
        available_citations=available,
        evidence=evidence,
        course_name=course_name,
    )
    degraded = _unique([*degraded, *outcome.degraded])

    before = await _mastery(session, scope, user_id=user_id, exclude_attempt_id=attempt.id)
    if outcome.score is None:
        # No grade was recorded, so there is no observation to log. The ungraded
        # placeholder row is removed rather than left to project as mastery 0.
        await session.delete(attempt)
        await session.flush()
        return AssessmentResult(
            quiz_attempt_id=attempt.id,
            item_id=item_id,
            quiz_id=quiz.id if quiz is not None else None,
            score=None,
            rubric=[],
            misconceptions=[],
            mastery_delta=0.0,
            progress_event_kind=None,
            degraded=degraded,
        )

    attempt.score = outcome.score
    attempt.rubric = [score.model_dump(mode="json") for score in outcome.rubric]
    attempt.misconceptions = [item_.model_dump_json() for item_ in outcome.misconceptions]
    kind = (
        ProgressEventKind.CONCEPT_STRUGGLED
        if outcome.score < STRUGGLE_THRESHOLD
        else ProgressEventKind.QUIZ_ATTEMPT
    )
    session.add(
        ProgressEvent(
            tenant_id=scope.tenant_id,
            user_id=user_id,
            course_id=attempt.course_id,
            concept_id=item.concept_id,
            kind=kind,
            mastery=outcome.score,
            weight=1.0,
            source="assessment",
            event_metadata={
                "quiz_id": str(quiz.id) if quiz is not None else None,
                "item_id": item_id,
                "attempt_id": str(attempt.id),
                "score": outcome.score,
            },
        )
    )
    await session.flush()

    after = await _mastery(session, scope, user_id=user_id, exclude_attempt_id=None)
    delta = 0.0
    if item.concept_id is not None:
        delta = round(after.get(item.concept_id, 0.0) - before.get(item.concept_id, 0.0), 6)

    return AssessmentResult(
        quiz_attempt_id=attempt.id,
        item_id=item_id,
        quiz_id=quiz.id if quiz is not None else None,
        score=outcome.score,
        rubric=outcome.rubric,
        misconceptions=outcome.misconceptions,
        mastery_delta=delta,
        progress_event_kind=kind.value,
        degraded=degraded,
    )


async def score_item(
    item: QuizItem,
    answer: str,
    *,
    settings: Settings,
    gateway: LLMGateway,
    available_citations: AbstractSet[str],
    evidence: str = "",
    course_name: str = "your course",
) -> ScoredOutcome:
    """Score one answer against the item's rubric, or return a typed failure.

    A multiple-choice item is graded here in Python and never reaches the model.
    Every other item is graded against its fixed rubric; the model's own total,
    if any, is ignored.
    """
    if item.item_type == "multiple_choice":
        score = score_multiple_choice(item, answer)
        return ScoredOutcome(
            score=score,
            rubric=_deterministic_rubric(item, score),
        )

    prompts = PromptLibrary(settings)
    system = prompts.render(
        EVALUATE_TEMPLATE,
        course_name=course_name,
        question=item.prompt,
        evidence=evidence,
    )
    request = LLMRequest(
        task=ModelTask.REASONING,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=_evaluation_user_message(item, answer)),
        ],
        temperature=settings.llm_temperature,
        purpose=EVALUATE_TEMPLATE,
        response_model=GeneratedScore,
    )
    try:
        response = await gateway.complete(request)
    except (ServiceUnavailableError, UpstreamError) as exc:
        logger.warning(
            "answer_evaluation_degraded",
            reason=LLM_UNAVAILABLE,
            exc_type=type(exc).__name__,
        )
        return ScoredOutcome(score=None, degraded=[LLM_UNAVAILABLE])

    generated = _coerce_scored(response.parsed, response.text)
    if generated is None:
        return ScoredOutcome(score=None, degraded=[STRUCTURED_OUTPUT_FAILED])

    rubric = validate_rubric(item.rubric, generated.criteria)
    if rubric is None:
        return ScoredOutcome(score=None, degraded=[INCOMPLETE_RUBRIC])

    misconceptions, dropped = ground_misconceptions(generated.misconceptions, available_citations)
    degraded = [UNGROUNDED_MISCONCEPTION_DROPPED] if dropped else []
    return ScoredOutcome(
        score=compute_total([(score.weight, score.score) for score in rubric]),
        rubric=rubric,
        misconceptions=misconceptions,
        degraded=degraded,
    )


def compute_total(scores: Sequence[tuple[float, float]]) -> float:
    """Weighted mean of ``(weight, score)`` pairs, clipped to ``[0, 1]``.

    The only arithmetic that produces a recorded grade. Each score is clipped
    before weighting, so a model returning ``1.5`` for a criterion cannot push
    the total above one, and a non-positive total weight yields zero rather than
    a division error.
    """
    total_weight = sum(weight for weight, _ in scores)
    if total_weight <= 0.0:
        return 0.0
    weighted = sum(weight * _clip01(score) for weight, score in scores)
    return round(_clip01(weighted / total_weight), 6)


def validate_rubric(
    item_rubric: Sequence[RubricCriterion],
    criteria: Sequence[GeneratedCriterion],
) -> list[RubricScore] | None:
    """Map the model's per-criterion scores onto the item's rubric, or reject.

    The comparison is set equality on criterion names: a missing criterion and
    an invented one are both failures. Duplicates are rejected too, because two
    scores for one criterion have no single meaning.
    """
    expected: dict[str, float] = {
        criterion.criterion: criterion.weight for criterion in item_rubric
    }
    provided: dict[str, GeneratedCriterion] = {}
    for criterion in criteria:
        if criterion.criterion in provided:
            return None
        provided[criterion.criterion] = criterion
    if set(provided) != set(expected):
        return None
    return [
        RubricScore(
            criterion=name,
            weight=weight,
            score=_clip01(provided[name].score),
            justification=provided[name].justification,
        )
        for name, weight in expected.items()
    ]


def ground_misconceptions(
    generated: Sequence[GeneratedMisconception],
    available_citations: AbstractSet[str],
) -> tuple[list[Misconception], bool]:
    """Keep only misconceptions whose citations resolve to the item's evidence.

    Returns the kept records and whether anything was dropped, so the caller can
    record the drop as a degradation rather than silently discarding a signal.
    """
    kept: list[Misconception] = []
    dropped = False
    for raw in generated:
        grounding = [citation for citation in raw.citation_ids if citation in available_citations]
        if not grounding:
            dropped = True
            continue
        try:
            kept.append(
                Misconception(
                    misconception_type=raw.misconception_type,
                    description=raw.description,
                    corrected_statement=raw.corrected_statement,
                    severity=raw.severity,
                    citation_ids=grounding,
                )
            )
        except PydanticValidationError:
            dropped = True
    return kept, dropped


def score_multiple_choice(item: QuizItem, answer: str) -> float:
    """Grade a multiple-choice answer deterministically.

    The submitted text may be the option's index, its letter or its exact text;
    anything else is incorrect rather than an error, because a student's typo is
    a wrong answer, not a failed request.
    """
    correct = item.correct_choice_index
    if correct is None:  # pragma: no cover - guaranteed by QuizItem validation
        return 0.0
    text = answer.strip()
    if text.isdigit():
        return 1.0 if int(text) == correct else 0.0
    if len(text) == 1 and text.upper() in "ABCD":
        return 1.0 if "ABCD".index(text.upper()) == correct else 0.0
    normalized = text.casefold()
    choices = [choice.strip().casefold() for choice in item.choices]
    if normalized in choices:
        return 1.0 if choices.index(normalized) == correct else 0.0
    return 0.0


def render_evidence(item: QuizItem, citations: Mapping[str, QuizCitation]) -> str:
    """Render the item's supporting passages as a delimited untrusted region.

    The evidence is exactly the citations the item carried, so a misconception
    can only be grounded in what the item was generated from.
    """
    blocks: list[str] = []
    for citation_id in item.citation_ids:
        citation = citations.get(citation_id)
        if citation is None:
            continue
        page = f' page="{citation.page}"' if citation.page is not None else ""
        blocks.append(
            f'{EVIDENCE_OPEN_TAG} id="{citation_id}" source="{citation.filename}"'
            f'{page} source_type="{citation.source_type}">\n'
            f"{citation.quote}\n"
            f"{EVIDENCE_CLOSE_TAG}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _deterministic_rubric(item: QuizItem, score: float) -> list[RubricScore]:
    return [
        RubricScore(
            criterion=criterion.criterion,
            weight=criterion.weight,
            score=score,
            justification="Graded deterministically against the correct option.",
        )
        for criterion in item.rubric
    ]


def _evaluation_user_message(item: QuizItem, answer: str) -> str:
    expected = {
        "multiple_choice": item.correct_choice_index,
        "short_answer": item.model_answer,
        "concept_check": item.correct_boolean,
    }[item.item_type]
    rubric = json.dumps(
        [
            {"criterion": criterion.criterion, "weight": criterion.weight}
            for criterion in item.rubric
        ]
    )
    return (
        f"Rubric: {rubric}\n"
        f"Expected answer: {json.dumps(expected)}\n"
        f"Student answer: {json.dumps(answer)}"
    )


def _coerce_scored(parsed: object, text: str) -> GeneratedScore | None:
    if isinstance(parsed, GeneratedScore):
        return parsed
    if not text:
        return None
    try:
        return GeneratedScore.model_validate_json(text)
    except PydanticValidationError:
        return None


async def _load_attempt(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> QuizAttempt:
    stmt = select(QuizAttempt).where(
        QuizAttempt.tenant_id == scope.tenant_id,
        QuizAttempt.id == attempt_id,
        QuizAttempt.user_id == user_id,
    )
    attempt = (await session.execute(stmt)).scalar_one_or_none()
    if attempt is None:
        msg = "Quiz attempt not found."
        raise NotFoundError(msg)
    return attempt


async def _load_quiz(
    session: AsyncSession, scope: TenantScope, *, quiz_id: uuid.UUID | None
) -> Quiz | None:
    if quiz_id is None:
        return None
    stmt = select(Quiz).where(Quiz.tenant_id == scope.tenant_id, Quiz.id == quiz_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def _find_item(quiz: Quiz | None, item_id: str) -> QuizItem | None:
    if quiz is None:
        return None
    for raw in quiz.items or []:
        try:
            item = QuizItem.model_validate(raw)
        except PydanticValidationError:
            continue
        if item.item_id == item_id:
            return item
    return None


def _citation_map(quiz: Quiz | None) -> dict[str, QuizCitation]:
    if quiz is None:
        return {}
    result: dict[str, QuizCitation] = {}
    for raw in quiz.citations or []:
        try:
            citation = QuizCitation.model_validate(raw)
        except PydanticValidationError:
            continue
        result[citation.citation_id] = citation
    return result


async def _clone_attempt(
    session: AsyncSession, scope: TenantScope, attempt: QuizAttempt
) -> QuizAttempt:
    clone = QuizAttempt(
        tenant_id=scope.tenant_id,
        user_id=attempt.user_id,
        course_id=attempt.course_id,
        quiz_id=attempt.quiz_id,
        item_id=attempt.item_id,
        concept_ids=list(attempt.concept_ids or []),
        answer=attempt.answer,
    )
    session.add(clone)
    await session.flush()
    return clone


async def _mastery(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    exclude_attempt_id: uuid.UUID | None,
) -> dict[uuid.UUID, float]:
    """Project the student's mastery, optionally excluding one in-flight attempt."""
    events = list(
        (
            await session.execute(
                select(ProgressEvent).where(
                    ProgressEvent.tenant_id == scope.tenant_id,
                    ProgressEvent.user_id == user_id,
                )
            )
        )
        .scalars()
        .all()
    )
    attempts = [
        attempt
        for attempt in (
            (
                await session.execute(
                    select(QuizAttempt).where(
                        QuizAttempt.tenant_id == scope.tenant_id,
                        QuizAttempt.user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if exclude_attempt_id is None or attempt.id != exclude_attempt_id
    ]
    return project_mastery(events, attempts)


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "EVALUATE_TEMPLATE",
    "REGRADE",
    "ScoredOutcome",
    "compute_total",
    "evaluate_answer",
    "ground_misconceptions",
    "render_evidence",
    "score_item",
    "score_multiple_choice",
    "validate_rubric",
]
