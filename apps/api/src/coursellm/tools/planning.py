"""``update_learning_plan`` — persist a roadmap revision.

Roadmap persistence is PR 11, so the handler returns a typed empty
:class:`RoadmapPlan` with ``degraded=["tool_unavailable"]``. The contract is
real: ``idempotency_key`` makes a retried write after a timeout a no-op rather
than a duplicate revision, and ``graph:read`` is required because a plan revision
that cannot read the prerequisite closure is not a plan.

The proposal/execution split for *consequential* revisions (one that discards
completed steps) is specified in ``security.md`` section 5; the typed
``ProposedAction`` return and the confirmation endpoint are part of PR 11. Until
then no revision is written at all, which is the safe direction: the failure mode
is an absent plan, never a silently replaced one.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)


class PlanStep(BaseModel):
    """One ordered roadmap step."""

    step_id: str | None = None
    concept_id: str
    title: str
    order: int = Field(ge=0)
    estimated_effort_minutes: int | None = Field(default=None, ge=0)
    completed: bool = False


class UpdateLearningPlanArgs(BaseModel):
    """Arguments for a roadmap revision. Scope is injected, never passed."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    goal_concept_id: str = Field(min_length=1, max_length=120)
    steps: list[PlanStep] = Field(default_factory=list, max_length=200)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)


class RoadmapPlan(BaseModel):
    """A persisted roadmap, with its revision number and step ids."""

    roadmap_id: str | None = None
    course_id: str
    goal_concept_id: str
    revision: int = 0
    steps: list[PlanStep] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    proposal: dict[str, Any] | None = None


async def update_learning_plan(args: UpdateLearningPlanArgs, ctx: ToolContext) -> RoadmapPlan:
    """Return an empty roadmap revision; persistence lands in PR 11."""
    return RoadmapPlan(
        roadmap_id=None,
        course_id=str(args.course_id),
        goal_concept_id=args.goal_concept_id,
        revision=0,
        steps=[],
        degraded=["tool_unavailable"],
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="update_learning_plan",
            description=(
                "Persist a roadmap revision idempotently on idempotency_key, "
                "preserving completed steps unless the caller proposes otherwise."
            ),
            parameters=UpdateLearningPlanArgs,
            required_permissions=frozenset({Permission.PLAN_WRITE, Permission.GRAPH_READ}),
            side_effects="write",
            timeout_ms=5000,
            handler=update_learning_plan,
        )
    )


__all__ = [
    "PlanStep",
    "RoadmapPlan",
    "UpdateLearningPlanArgs",
    "register",
    "update_learning_plan",
]
