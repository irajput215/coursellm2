"""``update_learning_plan`` — persist a real roadmap revision.

The Learning Planner agent calls this with a goal; the *ordering* is not taken
from the model. :func:`coursellm.learning.planner.build_roadmap` computes the
prerequisite closure, subtracts measured mastery and orders the remainder
deterministically, and this handler persists that plan. A model that supplied its
own order would be inventing a curriculum, which is exactly what
``docs/architecture/agent-architecture.md`` §5.2 forbids; when the model's steps
differ from the computed plan the result says so in ``degraded`` instead of
silently honouring them.

Idempotency is content-addressed. The plan for a given goal, graph and mastery map
is deterministic, so a retried write after a timeout produces a byte-identical
step set and the service returns the existing revision rather than creating a
duplicate. ``idempotency_key`` is accepted as part of the tool contract and is
echoed indirectly through the audit record; it is not stored on the roadmap
because the plan's own content is the stronger identity.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coursellm.db.models.learning import RoadmapStepStatus
from coursellm.db.tenancy import TenantScope
from coursellm.graph.repository import ConceptGraphRepository
from coursellm.learning.schemas import RoadmapPlan as PlannedRoadmap
from coursellm.services.roadmap import RoadmapDetail, RoadmapService
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


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _degraded_only(args: UpdateLearningPlanArgs, *reasons: str) -> RoadmapPlan:
    return RoadmapPlan(
        roadmap_id=None,
        course_id=str(args.course_id),
        goal_concept_id=args.goal_concept_id,
        revision=0,
        steps=[],
        degraded=list(reasons),
    )


def _to_tool_plan(
    args: UpdateLearningPlanArgs, detail: RoadmapDetail, plan: PlannedRoadmap
) -> RoadmapPlan:
    steps = [
        PlanStep(
            step_id=str(step.id),
            concept_id=str(step.concept_id) if step.concept_id is not None else "unresolved",
            title=step.title,
            order=step.order_index,
            estimated_effort_minutes=round(float(step.estimated_hours or 0.0) * 60),
            completed=step.status is RoadmapStepStatus.COMPLETED,
        )
        for step in detail.steps
    ]
    degraded = list(plan.degraded)
    requested = [step.concept_id for step in args.steps]
    computed = [step.concept_id for step in steps]
    if requested and requested != computed:
        # The model proposed an order that the deterministic planner did not
        # produce. The plan is authoritative; say so rather than honouring it.
        degraded.append("steps_recomputed")
    return RoadmapPlan(
        roadmap_id=str(detail.roadmap.id),
        course_id=str(args.course_id),
        goal_concept_id=args.goal_concept_id,
        revision=detail.roadmap.revision,
        steps=steps,
        degraded=degraded,
    )


async def update_learning_plan(args: UpdateLearningPlanArgs, ctx: ToolContext) -> RoadmapPlan:
    """Compute, persist and return a roadmap revision for the goal."""
    if ctx.session is None or ctx.user_id is None:
        return _degraded_only(args, "tool_unavailable")

    scope = TenantScope(ctx.tenant_id)
    goal_concept_id = _parse_uuid(args.goal_concept_id)
    goal_text = args.goal_concept_id
    if goal_concept_id is not None:
        concept = await ConceptGraphRepository(ctx.session, scope).get(goal_concept_id)
        if concept is None:
            return _degraded_only(args, "goal_unresolved")
        goal_text = concept.name

    service = RoadmapService(ctx.session, scope)
    roadmap, plan, _created = await service.create_or_reuse(
        user_id=ctx.user_id,
        settings=ctx.settings,
        course_id=args.course_id,
        goal_text=goal_text,
        goal_concept_id=goal_concept_id,
        gateway=ctx.gateway,
        reason=args.reason or "Plan revision requested by the planner agent.",
    )
    detail = await service.get_detail(roadmap_id=roadmap.id, user_id=ctx.user_id)
    return _to_tool_plan(args, detail, plan)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="update_learning_plan",
            description=(
                "Persist a deterministic roadmap revision for a goal, preserving "
                "completed steps and reusing the current revision when the plan is "
                "unchanged."
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
