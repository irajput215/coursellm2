"""Quiz and progress endpoints.

Six routes: generate and read a quiz, submit an answer, and read the progress
summary and attempt history. Two behaviours are choices rather than accidents:

* **Another tenant's or another user's quiz is a 404**, never a 403. Reporting
  "forbidden" would confirm that the resource exists, and the answer path is
  scoped to the caller's own quizzes for the same reason.
* **The progress summary is projected by the learning service**, not recomputed
  here. The router translates typed results into wire schemas and does no
  arithmetic; the projection has exactly one implementation.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from coursellm.api.deps import (
    AssessmentServiceDep,
    ContextDep,
    LLMGatewayDep,
    SettingsDep,
)
from coursellm.api.schemas.assessment import (
    AnswerSubmitRequest,
    AssessmentResultResponse,
    AttemptPageResponse,
    AttemptResponse,
    ProgressSummaryResponse,
    QuizCreateRequest,
    QuizResponse,
    assessment_response,
    quiz_response,
)
from coursellm.api.schemas.roadmap import NextActionResponse, RoadmapPositionResponse
from coursellm.db.models.learning import QuizAttempt

router = APIRouter(prefix="/quizzes", tags=["assessments"])
progress_router = APIRouter(prefix="/progress", tags=["progress"])


def _attempt_response(attempt: QuizAttempt) -> AttemptResponse:
    return AttemptResponse.model_validate(attempt)


@router.post(
    "",
    response_model=QuizResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a grounded quiz",
    description=(
        "Retrieves the course's own passages, reranks them and asks the model for "
        "items in one structured call. Every item carries the citation ids that "
        "support it; an item whose evidence does not resolve is dropped rather "
        "than padded, and the shortfall is reported."
    ),
)
async def create_quiz(
    payload: QuizCreateRequest,
    context: ContextDep,
    assessment_service: AssessmentServiceDep,
    settings: SettingsDep,
    gateway: LLMGatewayDep,
) -> QuizResponse:
    service = assessment_service
    draft = await service.generate(
        user_id=context.user_id,
        settings=settings,
        gateway=gateway,
        course_id=payload.course_id,
        concept_ids=payload.concept_ids or None,
        n_items=payload.n_items,
        difficulty=payload.difficulty,
        item_types=payload.item_types,
    )
    return quiz_response(draft)


@router.get(
    "/{quiz_id}",
    response_model=QuizResponse,
    summary="A previously generated quiz",
    description=(
        "A quiz belonging to another tenant or another user is reported as not "
        "found. Drafts are persisted, so this serves the exact items that were "
        "generated."
    ),
)
async def get_quiz(
    quiz_id: uuid.UUID,
    context: ContextDep,
    assessment_service: AssessmentServiceDep,
) -> QuizResponse:
    service = assessment_service
    draft = await service.get_quiz(quiz_id=quiz_id, user_id=context.user_id)
    return quiz_response(draft)


@router.post(
    "/{quiz_id}/items/{item_id}/answer",
    response_model=AssessmentResultResponse,
    summary="Submit and score an answer",
    description=(
        "Scores the answer against the item's rubric, writes exactly one "
        "progress event and records a quiz attempt with the rubric breakdown and "
        "misconceptions. A score the model computes is ignored: the total is "
        "computed from the criterion weights."
    ),
)
async def submit_answer(
    quiz_id: uuid.UUID,
    item_id: str,
    payload: AnswerSubmitRequest,
    context: ContextDep,
    assessment_service: AssessmentServiceDep,
    settings: SettingsDep,
    gateway: LLMGatewayDep,
) -> AssessmentResultResponse:
    service = assessment_service
    result = await service.submit_answer(
        quiz_id=quiz_id,
        item_id=item_id,
        answer=payload.answer,
        user_id=context.user_id,
        settings=settings,
        gateway=gateway,
    )
    return assessment_response(result)


@progress_router.get(
    "/summary",
    response_model=ProgressSummaryResponse,
    summary="The caller's progress summary",
    description=(
        "Mastery projected from the append-only evidence, weak and stale "
        "concepts, learning velocity, recent attempts, the current roadmap "
        "position and the next recommended action."
    ),
)
async def get_progress_summary(
    context: ContextDep,
    assessment_service: AssessmentServiceDep,
    course_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ProgressSummaryResponse:
    service = assessment_service
    overview = await service.progress_summary(user_id=context.user_id, course_id=course_id)
    recent = await service.recent_attempts(user_id=context.user_id)
    return ProgressSummaryResponse(
        mastery=overview.mastery,
        weak_concepts=list(overview.weak_concepts),
        stale_concepts=list(overview.stale_concepts),
        velocity=overview.velocity,
        attempt_counts=overview.attempt_counts,
        recent_attempts=[_attempt_response(attempt) for attempt in recent],
        current_position=(
            RoadmapPositionResponse.model_validate(overview.current_position)
            if overview.current_position is not None
            else None
        ),
        next_action=(
            NextActionResponse.model_validate(overview.next_action)
            if overview.next_action is not None
            else None
        ),
        degraded=list(overview.degraded),
    )


@progress_router.get(
    "/attempts",
    response_model=AttemptPageResponse,
    summary="Paginated attempt history",
    description="The caller's scored attempts, newest first.",
)
async def list_attempts(
    context: ContextDep,
    assessment_service: AssessmentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AttemptPageResponse:
    service = assessment_service
    page = await service.attempt_page(user_id=context.user_id, limit=limit, offset=offset)
    return AttemptPageResponse(
        items=[_attempt_response(attempt) for attempt in page.attempts],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


__all__ = ["progress_router", "router"]
