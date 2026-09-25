"""Roadmap and progress endpoints.

Five roadmap routes and one progress route. Two behaviours are worth stating
because they are choices, not accidents:

* **Creating a plan for an unchanged goal returns the existing revision**, with
  ``201`` only for a genuinely new one. A poll or a double-submit therefore cannot
  manufacture history.
* **Another tenant's or another user's roadmap is a 404**, never a 403. Reporting
  "forbidden" would confirm that the row exists.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from coursellm.api.deps import (
    ContextDep,
    LLMGatewayDep,
    RoadmapServiceDep,
    SettingsDep,
)
from coursellm.api.schemas.roadmap import (
    NextActionResponse,
    ProgressOverviewResponse,
    RoadmapAdaptRequest,
    RoadmapCreateRequest,
    RoadmapDetailResponse,
    RoadmapPositionResponse,
    RoadmapResponse,
    RoadmapStepResponse,
    RoadmapStepUpdateRequest,
)
from coursellm.db.models.learning import RoadmapStepStatus
from coursellm.services.roadmap import (
    ProgressOverview,
    RoadmapDetail,
)

router = APIRouter(prefix="/roadmaps", tags=["roadmaps"])
progress_router = APIRouter(prefix="/progress", tags=["progress"])


def _detail_response(detail: RoadmapDetail) -> RoadmapDetailResponse:
    base = RoadmapResponse.model_validate(detail.roadmap)
    return RoadmapDetailResponse(
        **base.model_dump(),
        steps=[RoadmapStepResponse.model_validate(step) for step in detail.steps],
    )


def _progress_response(overview: ProgressOverview) -> ProgressOverviewResponse:
    return ProgressOverviewResponse(
        mastery=overview.mastery,
        weak_concepts=list(overview.weak_concepts),
        stale_concepts=list(overview.stale_concepts),
        velocity=overview.velocity,
        attempt_counts=overview.attempt_counts,
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


@router.post(
    "",
    response_model=RoadmapDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a roadmap from a goal",
    description=(
        "Plans the prerequisite closure of the goal, subtracts concepts the student "
        "has mastery evidence for, and orders the remainder deterministically. "
        "Re-posting an unchanged goal returns the existing revision rather than "
        "creating a duplicate."
    ),
)
async def create_roadmap(
    payload: RoadmapCreateRequest,
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
    settings: SettingsDep,
    gateway: LLMGatewayDep,
    response: Response,
) -> RoadmapDetailResponse:
    service = roadmap_service
    roadmap, _plan, created = await service.create_or_reuse(
        user_id=context.user_id,
        settings=settings,
        course_id=payload.course_id,
        goal_text=payload.goal_text,
        goal_concept_id=payload.goal_concept_id,
        available_hours_per_week=payload.available_hours_per_week,
        gateway=gateway,
    )
    if not created:
        # An unchanged plan is the existing resource, not a new one.
        response.status_code = status.HTTP_200_OK
    detail = await service.get_detail(roadmap_id=roadmap.id, user_id=context.user_id)
    return _detail_response(detail)


@router.get(
    "",
    response_model=list[RoadmapResponse],
    summary="List the caller's roadmaps",
    description="The newest revision of each goal the caller has planned.",
)
async def list_roadmaps(
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
    course_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[RoadmapResponse]:
    service = roadmap_service
    rows = await service.list_latest(user_id=context.user_id, course_id=course_id)
    return [RoadmapResponse.model_validate(row) for row in rows]


@router.get(
    "/{roadmap_id}",
    response_model=RoadmapDetailResponse,
    summary="A roadmap and its ordered steps",
    description=(
        "A roadmap belonging to another tenant or another user is reported as not "
        "found. Reporting it as forbidden would confirm that it exists."
    ),
)
async def get_roadmap(
    roadmap_id: uuid.UUID,
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
) -> RoadmapDetailResponse:
    service = roadmap_service
    detail = await service.get_detail(roadmap_id=roadmap_id, user_id=context.user_id)
    return _detail_response(detail)


@router.post(
    "/{roadmap_id}/adapt",
    response_model=RoadmapDetailResponse,
    summary="Revise a roadmap from measured progress",
    description=(
        "Preserves completed steps, drops steps whose concepts are now mastered, "
        "re-orders unblocked steps, inserts newly surfaced prerequisites and records "
        "why. A no-op adaptation returns the current revision unchanged."
    ),
)
async def adapt_roadmap(
    roadmap_id: uuid.UUID,
    payload: RoadmapAdaptRequest,
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
    settings: SettingsDep,
) -> RoadmapDetailResponse:
    service = roadmap_service
    detail = await service.adapt_roadmap(
        roadmap_id=roadmap_id,
        user_id=context.user_id,
        settings=settings,
        reason=payload.reason,
    )
    return _detail_response(detail)


@router.patch(
    "/{roadmap_id}/steps/{step_id}",
    response_model=RoadmapDetailResponse,
    summary="Move a step to in progress or complete",
    description=(
        "Completing a step writes a progress event and marks dependents available; "
        "repeating it is a no-op. Completing a step whose prerequisite is not yet "
        "mastered is a typed 409."
    ),
)
async def update_step(
    roadmap_id: uuid.UUID,
    step_id: uuid.UUID,
    payload: RoadmapStepUpdateRequest,
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
) -> RoadmapDetailResponse:
    service = roadmap_service
    detail = await service.update_step(
        roadmap_id=roadmap_id,
        user_id=context.user_id,
        step_id=step_id,
        status=RoadmapStepStatus(payload.status),
    )
    return _detail_response(detail)


@progress_router.get(
    "",
    response_model=ProgressOverviewResponse,
    summary="The caller's progress",
    description=(
        "Mastery projected from the append-only evidence, weak and stale concepts, "
        "learning velocity, the current roadmap position and the next recommended "
        "action. Velocity is null when there is too little evidence for a rate."
    ),
)
async def get_progress(
    context: ContextDep,
    roadmap_service: RoadmapServiceDep,
    course_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ProgressOverviewResponse:
    service = roadmap_service
    overview = await service.progress_overview(user_id=context.user_id, course_id=course_id)
    return _progress_response(overview)


__all__ = ["progress_router", "router"]
