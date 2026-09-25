"""Roadmap use cases: create, read, adapt, advance a step, and read progress.

This is the only layer that knows how a :class:`~coursellm.learning.schemas.
RoadmapPlan` becomes rows, and it is where the invariants that span several tables
are enforced:

* **A revision is additive.** Changing a plan marks the current ``roadmaps`` row
  ``superseded`` and writes a new one; nothing is deleted, so the history of a
  student's plans stays readable.
* **A retry is not a new revision.** Re-planning a goal whose deterministic plan
  is byte-for-byte the current one returns the current roadmap instead of
  manufacturing a revision. That is what makes the endpoint safe to poll.
* **Completing a step is idempotent and cannot skip a prerequisite.** A second
  completion writes no second event, and a blocked step is a typed 409.
* **Cross-tenant and cross-user are both 404.** A roadmap belonging to another
  tenant or to another user in the same tenant is indistinguishable from one that
  does not exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings, get_settings
from coursellm.core.errors import NotFoundError
from coursellm.db.models.graph import Concept
from coursellm.db.models.learning import (
    ProgressEvent,
    ProgressEventKind,
    QuizAttempt,
    Roadmap,
    RoadmapStatus,
    RoadmapStep,
    RoadmapStepStatus,
)
from coursellm.db.tenancy import TenantScope
from coursellm.graph.repository import ConceptGraphRepository
from coursellm.learning.adaptation import adapt
from coursellm.learning.planner import (
    build_roadmap,
    estimate_step,
    load_mastery,
)
from coursellm.learning.progress import (
    DEFAULT_MASTERY_THRESHOLD,
    attempt_counts,
    last_seen_map,
    learning_velocity,
    mastery_weights,
    project_mastery,
    stale_concepts,
    weak_concepts,
)
from coursellm.learning.schemas import (
    AdaptedStep,
    BlockedStepError,
    InvalidStepTransitionError,
    PlannedStep,
    RoadmapPlan,
)
from coursellm.llm.gateway import LLMGateway

#: Default number of weak concepts returned by the progress overview.
WEAK_CONCEPT_LIMIT = 20


@dataclass(frozen=True, slots=True)
class RoadmapDetail:
    """A roadmap revision together with its ordered steps."""

    roadmap: Roadmap
    steps: tuple[RoadmapStep, ...]


@dataclass(frozen=True, slots=True)
class RoadmapPosition:
    """Where the student is in their active plan."""

    roadmap_id: uuid.UUID
    revision: int
    step_id: uuid.UUID
    concept_id: uuid.UUID | None
    order_index: int
    title: str
    status: RoadmapStepStatus


@dataclass(frozen=True, slots=True)
class NextAction:
    """The single next thing the student should do, with its rationale."""

    kind: str
    title: str
    rationale: str
    roadmap_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    concept_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProgressOverview:
    """The read-side projection surfaced by ``GET /api/v1/progress``."""

    mastery: dict[uuid.UUID, float]
    weak_concepts: tuple[uuid.UUID, ...]
    stale_concepts: tuple[uuid.UUID, ...]
    velocity: float | None
    attempt_counts: dict[uuid.UUID, int]
    current_position: RoadmapPosition | None
    next_action: NextAction | None
    degraded: tuple[str, ...] = ()


def _blocker_ids(step: RoadmapStep) -> list[uuid.UUID]:
    return [uuid.UUID(str(value)) for value in (step.blocked_by or [])]


def _plan_signature(steps: Sequence[RoadmapStep]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            step.concept_id,
            step.title,
            str(step.status),
            tuple(sorted(str(value) for value in (step.blocked_by or []))),
            round(float(step.estimated_hours or 0.0), 6),
        )
        for step in sorted(steps, key=lambda item: item.order_index)
    )


def _planned_signature(steps: Sequence[PlannedStep]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            step.concept_id,
            step.title,
            str(step.status),
            tuple(sorted(str(value) for value in step.blocked_by)),
            round(float(step.estimated_hours), 6),
        )
        for step in steps
    )


class RoadmapService:
    """Use cases over roadmaps, steps and the progress projection."""

    def __init__(
        self, session: AsyncSession, scope: TenantScope, settings: Settings | None = None
    ) -> None:
        self._session = session
        self._scope = scope
        # Optional so callers that do not care about tunable weights (tests,
        # scripts) keep working; the process settings are the production source.
        self._settings = settings if settings is not None else get_settings()

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._scope.tenant_id

    # -- writes -----------------------------------------------------------
    async def create_or_reuse(
        self,
        *,
        user_id: uuid.UUID,
        settings: Settings,
        course_id: uuid.UUID | None,
        goal_text: str,
        goal_concept_id: uuid.UUID | None = None,
        available_hours_per_week: float | None = None,
        gateway: LLMGateway | None = None,
        reason: str = "Initial plan",
    ) -> tuple[Roadmap, RoadmapPlan, bool]:
        """Plan a goal and persist it, reusing the current revision if unchanged.

        Returns the persisted revision, the plan that produced it (so the planner
        tool can report the plan's degradation markers without persisting them),
        and whether a new revision was actually written. The third value lets the
        HTTP layer answer ``200`` on a reuse instead of a misleading ``201``.

        Idempotency is **content-addressed**, not keyed by an ``idempotency_key``
        column: a re-post whose deterministic plan is byte-for-byte the current
        revision returns that revision rather than manufacturing history. The
        choice is deliberate — a caller-supplied key can be reused for a
        *different* plan, while hashing the plan itself cannot.
        """
        plan = await build_roadmap(
            self._session,
            self._scope,
            settings,
            gateway,
            user_id=user_id,
            course_id=course_id,
            goal_text=goal_text,
            goal_concept_id=goal_concept_id,
            available_hours_per_week=available_hours_per_week,
            reason=reason,
        )
        latest = await self._latest_for_goal(
            user_id=user_id, goal_concept_id=plan.goal_concept_id, goal_text=plan.goal_text
        )
        if latest is not None:
            existing_steps = await self._steps_for(latest.id)
            if _plan_signature(existing_steps) == _planned_signature(plan.steps):
                return latest, plan, False

        revision = latest.revision + 1 if latest is not None else 1
        if latest is not None:
            latest.status = RoadmapStatus.SUPERSEDED
        roadmap = Roadmap(
            tenant_id=self.tenant_id,
            user_id=user_id,
            course_id=course_id,
            goal_concept_id=plan.goal_concept_id,
            goal_text=plan.goal_text,
            revision=revision,
            status=RoadmapStatus.ACTIVE,
            estimated_hours=plan.estimated_hours,
            reason=plan.reason,
        )
        self._session.add(roadmap)
        await self._session.flush()
        await self._write_planned_steps(roadmap, plan.steps)
        await self._session.flush()
        return roadmap, plan, True

    async def adapt_roadmap(
        self,
        *,
        roadmap_id: uuid.UUID,
        user_id: uuid.UUID,
        settings: Settings,
        reason: str,
    ) -> RoadmapDetail:
        """Revise a roadmap from measured progress, or return it unchanged."""
        detail = await self.get_detail(roadmap_id=roadmap_id, user_id=user_id)
        roadmap = detail.roadmap
        mastery = await load_mastery(
            self._session,
            self._scope,
            user_id=user_id,
            weights=mastery_weights(self._settings),
        )
        events = await self._events_for(user_id)
        velocity = learning_velocity(events, datetime.now(UTC))
        candidates = await self._new_candidate_steps(roadmap, detail.steps, mastery, settings)
        result = adapt(
            roadmap,
            detail.steps,
            mastery=mastery,
            velocity=velocity,
            reason=reason,
            candidates=candidates,
        )
        if not result.changed:
            return detail

        roadmap.status = RoadmapStatus.SUPERSEDED
        revised = Roadmap(
            tenant_id=self.tenant_id,
            user_id=roadmap.user_id,
            course_id=roadmap.course_id,
            goal_concept_id=roadmap.goal_concept_id,
            goal_text=roadmap.goal_text,
            revision=result.revision,
            status=RoadmapStatus.ACTIVE,
            estimated_hours=round(sum(step.estimated_hours for step in result.steps), 4),
            reason=result.reason,
        )
        self._session.add(revised)
        await self._session.flush()
        self._write_adapted_steps(revised, result.steps)
        await self._session.flush()
        return RoadmapDetail(roadmap=revised, steps=await self._steps_for(revised.id))

    async def update_step(
        self,
        *,
        roadmap_id: uuid.UUID,
        user_id: uuid.UUID,
        step_id: uuid.UUID,
        status: RoadmapStepStatus,
    ) -> RoadmapDetail:
        """Move one step to in-progress or completed, with its side effects."""
        roadmap = await self._get_owned(roadmap_id, user_id)
        if roadmap is None:
            msg = "Roadmap not found."
            raise NotFoundError(msg)
        step = await self._get_step(roadmap.id, step_id)
        if step is None:
            msg = "Roadmap step not found."
            raise NotFoundError(msg)

        mastery = await load_mastery(
            self._session,
            self._scope,
            user_id=user_id,
            weights=mastery_weights(self._settings),
        )
        if status is RoadmapStepStatus.COMPLETED:
            if step.status is RoadmapStepStatus.COMPLETED:
                # Idempotent: a repeated completion is a no-op, not a second event.
                return RoadmapDetail(roadmap=roadmap, steps=await self._steps_for(roadmap.id))
            blockers = [
                concept_id
                for concept_id in _blocker_ids(step)
                if mastery.get(concept_id, 0.0) < DEFAULT_MASTERY_THRESHOLD
            ]
            if blockers:
                msg = (
                    "This step is blocked by a prerequisite that is not yet mastered. "
                    "Complete or master the prerequisite first."
                )
                raise BlockedStepError(msg)
            step.status = RoadmapStepStatus.COMPLETED
            step.completed_at = datetime.now(UTC)
            self._emit_completion_event(roadmap, step)
            await self._session.flush()
            await self._unblock_dependents(roadmap, step, mastery)
        else:
            if step.status is RoadmapStepStatus.COMPLETED:
                msg = "A completed step cannot be moved back to in progress."
                raise InvalidStepTransitionError(msg)
            blockers = [
                concept_id
                for concept_id in _blocker_ids(step)
                if mastery.get(concept_id, 0.0) < DEFAULT_MASTERY_THRESHOLD
            ]
            if blockers:
                msg = "This step is blocked by a prerequisite that is not yet mastered."
                raise BlockedStepError(msg)
            step.status = RoadmapStepStatus.IN_PROGRESS

        await self._session.flush()
        roadmap.status = await self._roadmap_status(roadmap)
        await self._session.flush()
        return RoadmapDetail(roadmap=roadmap, steps=await self._steps_for(roadmap.id))

    # -- reads ------------------------------------------------------------
    async def get_detail(self, *, roadmap_id: uuid.UUID, user_id: uuid.UUID) -> RoadmapDetail:
        roadmap = await self._get_owned(roadmap_id, user_id)
        if roadmap is None:
            msg = "Roadmap not found."
            raise NotFoundError(msg)
        return RoadmapDetail(roadmap=roadmap, steps=await self._steps_for(roadmap.id))

    async def list_latest(
        self, *, user_id: uuid.UUID, course_id: uuid.UUID | None = None
    ) -> list[Roadmap]:
        """The newest revision of each goal's roadmap for the caller."""
        stmt = select(Roadmap).where(
            Roadmap.tenant_id == self.tenant_id, Roadmap.user_id == user_id
        )
        if course_id is not None:
            stmt = stmt.where(Roadmap.course_id == course_id)
        rows = list((await self._session.execute(stmt)).scalars().all())
        newest: dict[tuple[uuid.UUID | None, str], Roadmap] = {}
        for roadmap in rows:
            key = (roadmap.goal_concept_id, roadmap.goal_text)
            current = newest.get(key)
            if current is None or roadmap.revision > current.revision:
                newest[key] = roadmap
        return sorted(
            newest.values(),
            key=lambda item: (item.created_at, str(item.id)),
            reverse=True,
        )

    async def progress_overview(
        self, *, user_id: uuid.UUID, course_id: uuid.UUID | None = None
    ) -> ProgressOverview:
        """Mastery, weak/stale concepts, velocity and the next recommended action."""
        now = datetime.now(UTC)
        events = await self._events_for(user_id)
        attempts = await self._attempts_for(user_id)
        mastery = project_mastery(events, attempts, weights=mastery_weights(self._settings))
        seen = last_seen_map(events, attempts)
        weak = weak_concepts(mastery, DEFAULT_MASTERY_THRESHOLD)[:WEAK_CONCEPT_LIMIT]
        stale = stale_concepts(mastery, seen, now)

        roadmap = await self._active_roadmap(user_id, course_id)
        position: RoadmapPosition | None = None
        steps: tuple[RoadmapStep, ...] = ()
        if roadmap is not None:
            steps = await self._steps_for(roadmap.id)
            position = self._position(roadmap, steps)

        next_action = await self._next_action(roadmap, position, weak)
        degraded: list[str] = []
        if not events and not attempts:
            degraded.append("no_progress_evidence")
        return ProgressOverview(
            mastery=mastery,
            weak_concepts=tuple(weak),
            stale_concepts=tuple(stale),
            velocity=learning_velocity(events, now),
            attempt_counts=attempt_counts(attempts),
            current_position=position,
            next_action=next_action,
            degraded=tuple(degraded),
        )

    # -- internals: persistence ------------------------------------------
    async def _write_planned_steps(self, roadmap: Roadmap, steps: Sequence[PlannedStep]) -> None:
        for step in steps:
            self._session.add(
                RoadmapStep(
                    tenant_id=self.tenant_id,
                    roadmap_id=roadmap.id,
                    concept_id=step.concept_id,
                    order_index=step.order_index,
                    title=step.title,
                    description=step.description,
                    status=step.status,
                    blocked_by=[str(concept_id) for concept_id in step.blocked_by],
                    estimated_hours=step.estimated_hours,
                )
            )

    def _write_adapted_steps(self, roadmap: Roadmap, steps: Sequence[AdaptedStep]) -> None:
        for step in steps:
            self._session.add(
                RoadmapStep(
                    tenant_id=self.tenant_id,
                    roadmap_id=roadmap.id,
                    concept_id=step.concept_id,
                    order_index=step.order_index,
                    title=step.title,
                    description=step.description,
                    status=step.status,
                    blocked_by=[str(concept_id) for concept_id in step.blocked_by],
                    estimated_hours=step.estimated_hours,
                    completed_at=step.completed_at,
                )
            )

    def _emit_completion_event(self, roadmap: Roadmap, step: RoadmapStep) -> None:
        self._session.add(
            ProgressEvent(
                tenant_id=self.tenant_id,
                user_id=roadmap.user_id,
                course_id=roadmap.course_id,
                concept_id=step.concept_id,
                kind=ProgressEventKind.ROADMAP_STEP_COMPLETED,
                mastery=1.0,
                weight=1.0,
                source="roadmap_step",
                event_metadata={
                    "roadmap_id": str(roadmap.id),
                    "step_id": str(step.id),
                    "order_index": step.order_index,
                },
            )
        )

    async def _unblock_dependents(
        self, roadmap: Roadmap, completed: RoadmapStep, mastery: Mapping[uuid.UUID, float]
    ) -> None:
        """Mark dependents available once their last prerequisite is satisfied."""
        satisfied = {
            concept_id
            for concept_id, value in mastery.items()
            if value >= DEFAULT_MASTERY_THRESHOLD
        }
        satisfied.update(
            step.concept_id
            for step in await self._steps_for(roadmap.id)
            if step.status is RoadmapStepStatus.COMPLETED and step.concept_id is not None
        )
        for step in await self._steps_for(roadmap.id):
            if step.status is RoadmapStepStatus.COMPLETED:
                continue
            remaining = [
                concept_id for concept_id in _blocker_ids(step) if concept_id not in satisfied
            ]
            # Keep the stored blockers in step with reality: a satisfied
            # prerequisite is no longer a blocker, and the API reports the live set.
            step.blocked_by = [str(concept_id) for concept_id in remaining]
            if remaining:
                step.status = RoadmapStepStatus.BLOCKED
            elif step.status in {RoadmapStepStatus.BLOCKED, RoadmapStepStatus.PENDING}:
                step.status = RoadmapStepStatus.AVAILABLE

    async def _roadmap_status(self, roadmap: Roadmap) -> RoadmapStatus:
        steps = await self._steps_for(roadmap.id)
        if steps and all(step.status is RoadmapStepStatus.COMPLETED for step in steps):
            return RoadmapStatus.COMPLETED
        if roadmap.status is RoadmapStatus.COMPLETED:
            return RoadmapStatus.ACTIVE
        return roadmap.status

    # -- internals: reads ------------------------------------------------
    async def _get_owned(self, roadmap_id: uuid.UUID, user_id: uuid.UUID) -> Roadmap | None:
        stmt = select(Roadmap).where(
            Roadmap.tenant_id == self.tenant_id,
            Roadmap.id == roadmap_id,
            Roadmap.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _steps_for(self, roadmap_id: uuid.UUID) -> tuple[RoadmapStep, ...]:
        stmt = (
            select(RoadmapStep)
            .where(
                RoadmapStep.tenant_id == self.tenant_id,
                RoadmapStep.roadmap_id == roadmap_id,
            )
            .order_by(RoadmapStep.order_index, RoadmapStep.id)
        )
        return tuple((await self._session.execute(stmt)).scalars().all())

    async def _get_step(self, roadmap_id: uuid.UUID, step_id: uuid.UUID) -> RoadmapStep | None:
        stmt = select(RoadmapStep).where(
            RoadmapStep.tenant_id == self.tenant_id,
            RoadmapStep.roadmap_id == roadmap_id,
            RoadmapStep.id == step_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _latest_for_goal(
        self,
        *,
        user_id: uuid.UUID,
        goal_concept_id: uuid.UUID | None,
        goal_text: str,
    ) -> Roadmap | None:
        stmt = select(Roadmap).where(
            Roadmap.tenant_id == self.tenant_id,
            Roadmap.user_id == user_id,
            Roadmap.goal_text == goal_text,
        )
        if goal_concept_id is None:
            stmt = stmt.where(Roadmap.goal_concept_id.is_(None))
        else:
            stmt = stmt.where(Roadmap.goal_concept_id == goal_concept_id)
        stmt = stmt.order_by(Roadmap.revision.desc(), Roadmap.created_at.desc()).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def _active_roadmap(
        self, user_id: uuid.UUID, course_id: uuid.UUID | None
    ) -> Roadmap | None:
        stmt = (
            select(Roadmap)
            .where(
                Roadmap.tenant_id == self.tenant_id,
                Roadmap.user_id == user_id,
                Roadmap.status == RoadmapStatus.ACTIVE,
            )
            .order_by(Roadmap.created_at.desc())
            .limit(1)
        )
        if course_id is not None:
            stmt = stmt.where(Roadmap.course_id == course_id)
        return (await self._session.execute(stmt)).scalars().first()

    async def _events_for(self, user_id: uuid.UUID) -> list[ProgressEvent]:
        stmt = select(ProgressEvent).where(
            ProgressEvent.tenant_id == self.tenant_id,
            ProgressEvent.user_id == user_id,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _attempts_for(self, user_id: uuid.UUID) -> list[QuizAttempt]:
        stmt = select(QuizAttempt).where(
            QuizAttempt.tenant_id == self.tenant_id,
            QuizAttempt.user_id == user_id,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _new_candidate_steps(
        self,
        roadmap: Roadmap,
        steps: Sequence[RoadmapStep],
        mastery: Mapping[uuid.UUID, float],
        settings: Settings,
    ) -> list[PlannedStep]:
        """Prerequisite concepts that are not yet planned and not yet mastered."""
        if roadmap.goal_concept_id is None:
            return []
        repo = ConceptGraphRepository(self._session, self._scope)
        closure = await repo.prerequisite_closure(
            roadmap.goal_concept_id,
            max_depth=settings.graph_max_depth,
            min_confidence=settings.graph_min_traversable_confidence,
        )
        edges = await repo.closure_edges(
            roadmap.goal_concept_id,
            max_depth=settings.graph_max_depth,
            min_confidence=settings.graph_min_traversable_confidence,
        )
        present = {step.concept_id for step in steps if step.concept_id is not None}
        present.update(
            concept_id
            for concept_id, value in mastery.items()
            if value >= DEFAULT_MASTERY_THRESHOLD
        )
        candidates: list[PlannedStep] = []
        for node in closure:
            if node.concept_id in present:
                continue
            prerequisites = tuple(
                sorted(
                    edge.target_concept_id
                    for edge in edges
                    if edge.source_concept_id == node.concept_id
                )
            )
            estimate = estimate_step(node.difficulty, len(prerequisites))
            concept = await repo.get(node.concept_id)
            description = (
                concept.description
                if concept is not None and concept.description
                else f"Study {node.name}."
            )
            candidates.append(
                PlannedStep(
                    concept_id=node.concept_id,
                    title=node.name[:300],
                    description=description,
                    order_index=0,
                    status=RoadmapStepStatus.PENDING,
                    blocked_by=(),
                    estimated_hours=estimate.hours,
                    difficulty=node.difficulty,
                    depth=node.depth,
                    prerequisites=prerequisites,
                )
            )
        return candidates

    def _position(self, roadmap: Roadmap, steps: Sequence[RoadmapStep]) -> RoadmapPosition | None:
        for step in steps:
            if step.status is not RoadmapStepStatus.COMPLETED:
                return RoadmapPosition(
                    roadmap_id=roadmap.id,
                    revision=roadmap.revision,
                    step_id=step.id,
                    concept_id=step.concept_id,
                    order_index=step.order_index,
                    title=step.title,
                    status=step.status,
                )
        return None

    async def _next_action(
        self,
        roadmap: Roadmap | None,
        position: RoadmapPosition | None,
        weak: Sequence[uuid.UUID],
    ) -> NextAction | None:
        if roadmap is not None and position is not None:
            return NextAction(
                kind="complete_step",
                title=position.title,
                rationale=(
                    "This is the next available step in your active roadmap."
                    if position.status is not RoadmapStepStatus.BLOCKED
                    else "This step is blocked until its prerequisite is mastered."
                ),
                roadmap_id=roadmap.id,
                step_id=position.step_id,
                concept_id=position.concept_id,
            )
        if weak:
            concept_id = weak[0]
            name = await self._concept_name(concept_id)
            return NextAction(
                kind="review_concept",
                title=name,
                rationale="This concept has the weakest evidence in your progress history.",
                concept_id=concept_id,
            )
        return None

    async def _concept_name(self, concept_id: uuid.UUID) -> str:
        stmt = select(Concept).where(Concept.tenant_id == self.tenant_id, Concept.id == concept_id)
        concept = (await self._session.execute(stmt)).scalar_one_or_none()
        return concept.name if concept is not None else str(concept_id)


__all__ = [
    "NextAction",
    "ProgressOverview",
    "RoadmapDetail",
    "RoadmapPosition",
    "RoadmapService",
]
