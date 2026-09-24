"""Typed domain objects for planning, progress and adaptation.

These are the shapes the learning layer passes between its pure functions and
its persistence layer. They are deliberately *not* ORM rows: the planner and the
adapter can then be exercised without a database, and the service layer is the
only place that knows how a plan becomes a row.

``unresolved_cycle`` and ``degraded`` are first-class rather than exceptions. A
roadmap that cannot be fully ordered is still useful, and hiding that fact would
be worse than stating it: the review UI and the API both surface it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from coursellm.core.errors import ConflictError, NotFoundError
from coursellm.db.models.learning import RoadmapStepStatus


class GoalUnresolvedError(NotFoundError):
    """The caller named a specific goal concept that does not exist in the tenant.

    Distinct from the degraded path: a free-text goal that matches nothing still
    produces a plan from course metadata, because there is something useful to
    say. A goal *id* that matches nothing is a bad request, not an empty answer.
    """

    error_code = "goal_unresolved"


class RoadmapCycleError(ConflictError):
    """A prerequisite cycle makes a total order impossible.

    The default planner path returns the ordered prefix plus an
    ``unresolved_cycle`` marker; callers that must not proceed with a partial
    plan can ask for strict ordering and get this instead.
    """

    error_code = "roadmap_cycle"


class BlockedStepError(ConflictError):
    """A step was completed while a prerequisite was still unmastered."""

    error_code = "step_blocked"


class InvalidStepTransitionError(ConflictError):
    """The requested status transition is not allowed for this step."""

    error_code = "invalid_step_transition"


@dataclass(frozen=True, slots=True)
class StepEstimate:
    """The deterministic effort estimate for one concept.

    Kept as a value object so the formula's inputs are auditable next to its
    output, which is what makes "why is this three hours?" answerable.
    """

    base_hours: float
    prerequisite_hours: float
    prerequisite_count: int
    hours: float
    clipped: bool


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """One step the planner has ordered, before it is persisted.

    ``prerequisites`` is every direct prerequisite inside the closure;
    ``blocked_by`` is the subset that is neither mastered nor absent from the
    plan, i.e. the set that actually gates this step.
    """

    concept_id: uuid.UUID | None
    title: str
    description: str
    order_index: int
    status: RoadmapStepStatus
    blocked_by: tuple[uuid.UUID, ...]
    estimated_hours: float
    difficulty: int | None = None
    depth: int | None = None
    never_assessed: bool = False
    prerequisites: tuple[uuid.UUID, ...] = ()

    @property
    def estimated_effort_minutes(self) -> int:
        """Whole minutes, for the tool schema which speaks in minutes."""
        return round(self.estimated_hours * 60)


@dataclass(frozen=True, slots=True)
class RoadmapPlan:
    """A complete, ordered plan for one goal, with how it degraded."""

    course_id: uuid.UUID | None
    user_id: uuid.UUID
    goal_concept_id: uuid.UUID | None
    goal_text: str
    reason: str
    steps: tuple[PlannedStep, ...]
    estimated_hours: float
    estimated_weeks: float | None = None
    degraded: tuple[str, ...] = ()
    unresolved_cycle: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class StepSnapshot:
    """One existing step as the adapter sees it.

    A snapshot rather than the ORM row so that adaptation is a pure function and
    its tests are hand-computed rather than database-shaped. ``prerequisites`` is
    the stored ``blocked_by`` set: the plan-time unmastered prerequisites.
    """

    step_id: uuid.UUID | None
    concept_id: uuid.UUID | None
    title: str
    description: str
    status: RoadmapStepStatus
    blocked_by: tuple[uuid.UUID, ...]
    estimated_hours: float
    order_index: int
    prerequisites: tuple[uuid.UUID, ...] = ()
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdaptedStep:
    """One step of the revised plan."""

    concept_id: uuid.UUID | None
    title: str
    description: str
    order_index: int
    status: RoadmapStepStatus
    blocked_by: tuple[uuid.UUID, ...]
    estimated_hours: float
    source_step_id: uuid.UUID | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdaptationResult:
    """The outcome of :func:`coursellm.learning.adaptation.adapt`.

    ``changed`` is what the service keys on: a no-op adaptation must not create a
    new revision, or every poll of the endpoint would manufacture history.
    """

    changed: bool
    revision: int
    reason: str
    steps: tuple[AdaptedStep, ...]
    dropped_concept_ids: tuple[uuid.UUID, ...] = ()
    inserted_concept_ids: tuple[uuid.UUID, ...] = ()
    degraded: tuple[str, ...] = ()


__all__ = [
    "AdaptationResult",
    "AdaptedStep",
    "BlockedStepError",
    "GoalUnresolvedError",
    "InvalidStepTransitionError",
    "PlannedStep",
    "RoadmapCycleError",
    "RoadmapPlan",
    "StepEstimate",
    "StepSnapshot",
]
