"""Request and response schemas for roadmaps and progress.

**What is deliberately absent.** A roadmap response carries the ordered steps, the
blocking concept ids and the deterministic effort estimate, but not the model
prompt that wrote a step's prose, and not the mastery-evidence rows behind a
projection. Those are internal; the numbers and the plan are what a student acts
on, and the evidence stays in the append-only log where it can be audited.

``blocked_by`` is a list of concept ids rather than step ids. The blocking
relationship is a curriculum fact, not a property of one revision's rows, so it
stays stable across the revisions an adaptation creates.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.db.models.learning import RoadmapStatus, RoadmapStepStatus


class RoadmapCreateRequest(BaseModel):
    """Create (or reuse) a roadmap from a learning goal."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID | None = Field(
        default=None, description="Restrict the plan to one of the caller's courses."
    )
    goal_text: str = Field(
        min_length=1,
        max_length=500,
        description="What the student wants to be able to do, in their own words.",
    )
    goal_concept_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "An explicit concept to plan towards. When supplied it must resolve, or "
            "the request fails rather than planning something else."
        ),
    )
    available_hours_per_week: float | None = Field(
        default=None,
        gt=0,
        le=168,
        description="Optional weekly budget, used only to estimate how many weeks the plan spans.",
    )


class RoadmapAdaptRequest(BaseModel):
    """Ask for a revision now, recording why it was requested."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class RoadmapStepUpdateRequest(BaseModel):
    """Move one step to in progress or completed."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["in_progress", "completed"]


class RoadmapStepResponse(BaseModel):
    """One ordered step, with its blockers and deterministic estimate."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    concept_id: uuid.UUID | None
    order_index: int
    title: str
    description: str
    status: RoadmapStepStatus
    blocked_by: list[uuid.UUID]
    estimated_hours: float
    completed_at: datetime | None


class RoadmapResponse(BaseModel):
    """One roadmap revision, without its steps."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID | None
    goal_concept_id: uuid.UUID | None
    goal_text: str
    revision: int
    status: RoadmapStatus
    estimated_hours: float
    reason: str
    created_at: datetime


class RoadmapDetailResponse(RoadmapResponse):
    """A roadmap revision and its steps, in order."""

    steps: list[RoadmapStepResponse]


class NextActionResponse(BaseModel):
    """The single next thing the student should do."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    title: str
    rationale: str
    roadmap_id: uuid.UUID | None
    step_id: uuid.UUID | None
    concept_id: uuid.UUID | None


class RoadmapPositionResponse(BaseModel):
    """Where the student is in their active plan."""

    model_config = ConfigDict(from_attributes=True)

    roadmap_id: uuid.UUID
    revision: int
    step_id: uuid.UUID
    concept_id: uuid.UUID | None
    order_index: int
    title: str
    status: RoadmapStepStatus


class ProgressOverviewResponse(BaseModel):
    """Mastery, weak/stale concepts, velocity and the next recommended action."""

    mastery: dict[uuid.UUID, float]
    weak_concepts: list[uuid.UUID]
    stale_concepts: list[uuid.UUID]
    velocity: float | None
    attempt_counts: dict[uuid.UUID, int]
    current_position: RoadmapPositionResponse | None
    next_action: NextActionResponse | None
    degraded: list[str]


__all__ = [
    "NextActionResponse",
    "ProgressOverviewResponse",
    "RoadmapAdaptRequest",
    "RoadmapCreateRequest",
    "RoadmapDetailResponse",
    "RoadmapPositionResponse",
    "RoadmapResponse",
    "RoadmapStepResponse",
    "RoadmapStepUpdateRequest",
]
