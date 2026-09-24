"""Roadmap adaptation: revise a plan from what the student has actually learned.

The rules are the ones in ``docs/architecture/knowledge-graph.md`` §8 and
``docs/architecture/agent-architecture.md`` §5.2: an adaptation **preserves
completed work**, **drops** steps the student has since mastered, **re-orders**
steps whose prerequisites are now satisfied, **inserts** newly surfaced
prerequisite steps, recomputes ``blocked_by``, and records why.

Two properties matter more than the mechanics:

* **A no-op is a no-op.** ``changed`` is ``False`` when the revision would be
  identical, so polling the adapt endpoint cannot manufacture history. A new
  ``roadmaps`` row is written only when something actually changed.
* **Completed steps are never dropped.** Even when the student's mastery evidence
  says they know the material, the completed step is the record that they did the
  work. History is additive; the previous revision is marked ``superseded``, never
  deleted.

Adaptation is a pure function over snapshots of the current revision, so every
rule above is unit-testable without a database. The service layer is what turns
the result into rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from coursellm.db.models.learning import Roadmap, RoadmapStepStatus
from coursellm.learning.planner import topological_order
from coursellm.learning.progress import DEFAULT_MASTERY_THRESHOLD
from coursellm.learning.schemas import (
    AdaptationResult,
    AdaptedStep,
    PlannedStep,
    StepSnapshot,
)


@dataclass(slots=True)
class _Node:
    """Mutable working copy of one step during adaptation."""

    key: uuid.UUID
    concept_id: uuid.UUID | None
    title: str
    description: str
    status: RoadmapStepStatus
    estimated_hours: float
    order_index: int
    prerequisites: tuple[uuid.UUID, ...]
    completed_at: datetime | None
    source_step_id: uuid.UUID | None
    inserted: bool


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _normalise_mastery(mastery: Mapping[Any, float]) -> dict[uuid.UUID, float]:
    normalised: dict[uuid.UUID, float] = {}
    for key, value in mastery.items():
        concept_id = _as_uuid(key)
        if concept_id is not None:
            normalised[concept_id] = float(value)
    return normalised


def _snapshot_key(step: StepSnapshot) -> uuid.UUID:
    if step.step_id is not None:
        return step.step_id
    if step.concept_id is not None:
        return step.concept_id
    return uuid.uuid5(uuid.NAMESPACE_URL, f"roadmap-step:{step.title}:{step.order_index}")


def _as_status(value: Any) -> RoadmapStepStatus:
    try:
        return RoadmapStepStatus(str(value))
    except ValueError:
        return RoadmapStepStatus.PENDING


def snapshots_from_steps(steps: Sequence[Any]) -> tuple[StepSnapshot, ...]:
    """Convert ORM roadmap-step rows into the adapter's input snapshots."""
    snapshots: list[StepSnapshot] = []
    for step in steps:
        blocked = tuple(
            concept_id
            for concept_id in (_as_uuid(value) for value in (step.blocked_by or []))
            if concept_id is not None
        )
        snapshots.append(
            StepSnapshot(
                step_id=step.id,
                concept_id=step.concept_id,
                title=str(step.title),
                description=str(step.description or ""),
                status=_as_status(step.status),
                blocked_by=blocked,
                estimated_hours=float(step.estimated_hours or 0.0),
                order_index=int(step.order_index),
                prerequisites=blocked,
                completed_at=step.completed_at,
            )
        )
    return tuple(snapshots)


def adapt(
    roadmap: Roadmap,
    steps: Sequence[Any],
    *,
    mastery: Mapping[Any, float],
    velocity: float | None,
    reason: str,
    candidates: Sequence[PlannedStep] = (),
    mastery_threshold: float = DEFAULT_MASTERY_THRESHOLD,
) -> AdaptationResult:
    """Revise ``roadmap``'s steps in response to measured progress.

    ``steps`` may be ORM rows or :class:`~coursellm.learning.schemas.StepSnapshot`
    values; both are normalised. ``candidates`` are newly surfaced prerequisite
    steps the caller obtained from the graph, since adaptation must not traverse
    the graph itself.
    """
    snapshots: tuple[StepSnapshot, ...]
    if all(isinstance(step, StepSnapshot) for step in steps):
        snapshots = tuple(step for step in steps if isinstance(step, StepSnapshot))
    else:
        snapshots = snapshots_from_steps(steps)
    typed_snapshots: list[StepSnapshot] = list(snapshots)

    projected = _normalise_mastery(mastery)
    mastered = {concept_id for concept_id, value in projected.items() if value >= mastery_threshold}

    nodes: list[_Node] = []
    for snapshot in typed_snapshots:
        nodes.append(
            _Node(
                key=_snapshot_key(snapshot),
                concept_id=snapshot.concept_id,
                title=snapshot.title,
                description=snapshot.description,
                status=snapshot.status,
                estimated_hours=snapshot.estimated_hours,
                order_index=snapshot.order_index,
                prerequisites=snapshot.prerequisites,
                completed_at=snapshot.completed_at,
                source_step_id=snapshot.step_id,
                inserted=False,
            )
        )

    dropped: list[uuid.UUID] = []
    kept: list[_Node] = []
    for node in nodes:
        # Completed work is never dropped, even if the student now has mastery
        # evidence for it: the step is the record that the work was done.
        if node.status is RoadmapStepStatus.COMPLETED:
            kept.append(node)
            continue
        if node.concept_id is not None and node.concept_id in mastered:
            dropped.append(node.concept_id)
            continue
        kept.append(node)

    inserted: list[uuid.UUID] = []
    present_concepts = {node.concept_id for node in kept if node.concept_id is not None}
    for candidate in candidates:
        concept_id = candidate.concept_id
        if concept_id is None or concept_id in present_concepts or concept_id in mastered:
            continue
        key = uuid.uuid5(uuid.NAMESPACE_URL, f"roadmap-candidate:{concept_id}")
        kept.append(
            _Node(
                key=key,
                concept_id=concept_id,
                title=candidate.title,
                description=candidate.description,
                status=candidate.status,
                estimated_hours=candidate.estimated_hours,
                # Candidates are new: they sort after the existing steps unless a
                # dependency forces them earlier.
                order_index=len(typed_snapshots) + len(inserted),
                prerequisites=tuple(candidate.prerequisites) or tuple(candidate.blocked_by),
                completed_at=None,
                source_step_id=None,
                inserted=True,
            )
        )
        inserted.append(concept_id)
        present_concepts.add(concept_id)

    satisfied = set(mastered)
    satisfied.update(
        node.concept_id
        for node in kept
        if node.status is RoadmapStepStatus.COMPLETED and node.concept_id is not None
    )
    concept_to_key: dict[uuid.UUID, uuid.UUID] = {
        node.concept_id: node.key for node in kept if node.concept_id is not None
    }
    dependencies: dict[uuid.UUID, set[uuid.UUID]] = {}
    blockers: dict[uuid.UUID, tuple[uuid.UUID, ...]] = {}
    for node in kept:
        active_prerequisites = tuple(
            prerequisite for prerequisite in node.prerequisites if prerequisite not in satisfied
        )
        blockers[node.key] = tuple(sorted(active_prerequisites))
        dependencies[node.key] = {
            concept_to_key[prerequisite]
            for prerequisite in active_prerequisites
            if prerequisite in concept_to_key
        }

    priority: dict[uuid.UUID, tuple[Any, ...]] = {
        node.key: (node.order_index, node.inserted, node.title, str(node.key)) for node in kept
    }
    ordered_keys, _unresolved = topological_order(
        [node.key for node in kept], dependencies, priority
    )
    by_key = {node.key: node for node in kept}
    ordered = [by_key[key] for key in ordered_keys]

    first_unfinished = next(
        (
            index
            for index, node in enumerate(ordered)
            if node.status is not RoadmapStepStatus.COMPLETED
        ),
        None,
    )
    adapted: list[AdaptedStep] = []
    for index, node in enumerate(ordered):
        step_blockers = blockers[node.key]
        if node.status is RoadmapStepStatus.COMPLETED:
            status = RoadmapStepStatus.COMPLETED
        elif step_blockers:
            status = RoadmapStepStatus.BLOCKED
        elif index == first_unfinished and node.status is RoadmapStepStatus.IN_PROGRESS:
            status = RoadmapStepStatus.IN_PROGRESS
        elif index == first_unfinished:
            status = RoadmapStepStatus.AVAILABLE
        else:
            status = RoadmapStepStatus.PENDING
        adapted.append(
            AdaptedStep(
                concept_id=node.concept_id,
                title=node.title,
                description=node.description,
                order_index=index,
                status=status,
                blocked_by=step_blockers,
                estimated_hours=node.estimated_hours,
                source_step_id=node.source_step_id,
                completed_at=node.completed_at,
            )
        )

    changed = _has_changed(typed_snapshots, adapted)
    degraded: list[str] = []
    if velocity is None:
        # Too little evidence for a rate. Recorded so the caller knows the
        # adaptation ran without a progress signal rather than guessing one.
        degraded.append("velocity_unknown")

    return AdaptationResult(
        changed=changed,
        revision=int(roadmap.revision) + 1 if changed else int(roadmap.revision),
        reason=reason,
        steps=tuple(adapted),
        dropped_concept_ids=tuple(dropped),
        inserted_concept_ids=tuple(inserted),
        degraded=tuple(degraded),
    )


def _has_changed(before: Sequence[StepSnapshot], after: Sequence[AdaptedStep]) -> bool:
    """Compare two revisions on the fields an adaptation is allowed to change."""
    left = tuple(
        (
            snapshot.concept_id,
            snapshot.order_index,
            str(snapshot.status),
            tuple(sorted(snapshot.blocked_by)),
            round(snapshot.estimated_hours, 6),
        )
        for snapshot in before
    )
    right = tuple(
        (
            step.concept_id,
            step.order_index,
            str(step.status),
            tuple(sorted(step.blocked_by)),
            round(step.estimated_hours, 6),
        )
        for step in after
    )
    return left != right


__all__ = ["adapt", "snapshots_from_steps"]
