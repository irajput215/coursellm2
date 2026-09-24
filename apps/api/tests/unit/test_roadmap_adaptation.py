"""Unit tests for roadmap adaptation.

Adaptation is a pure function over step snapshots, so every rule — completed
steps survive, mastered steps drop, dependents unblock, a no-op stays a no-op —
is asserted without a database or an HTTP request.
"""

from __future__ import annotations

import uuid

import pytest

from coursellm.db.models.learning import Roadmap, RoadmapStepStatus
from coursellm.learning.adaptation import adapt
from coursellm.learning.schemas import PlannedStep, StepSnapshot

pytestmark = pytest.mark.unit

A = uuid.uuid4()
B = uuid.uuid4()
C = uuid.uuid4()
NEW = uuid.uuid4()


def _roadmap(revision: int = 1) -> Roadmap:
    return Roadmap(
        user_id=uuid.uuid4(),
        goal_text="Transformers",
        revision=revision,
        status="active",
        estimated_hours=6.0,
        reason="Initial plan",
    )


def _snapshot(
    concept_id: uuid.UUID,
    *,
    order_index: int,
    status: RoadmapStepStatus,
    blocked_by: tuple[uuid.UUID, ...] = (),
    prerequisites: tuple[uuid.UUID, ...] | None = None,
    title: str | None = None,
    estimated_hours: float = 2.0,
    completed_at: object | None = None,
) -> StepSnapshot:
    return StepSnapshot(
        step_id=uuid.uuid4(),
        concept_id=concept_id,
        title=title or f"Step {concept_id}",
        description="description",
        status=status,
        blocked_by=blocked_by,
        estimated_hours=estimated_hours,
        order_index=order_index,
        prerequisites=blocked_by if prerequisites is None else prerequisites,
        completed_at=completed_at,  # type: ignore[arg-type]
    )


def _three_step_chain() -> list[StepSnapshot]:
    return [
        _snapshot(A, order_index=0, status=RoadmapStepStatus.AVAILABLE),
        _snapshot(B, order_index=1, status=RoadmapStepStatus.BLOCKED, blocked_by=(A,)),
        _snapshot(C, order_index=2, status=RoadmapStepStatus.BLOCKED, blocked_by=(B,)),
    ]


class TestCompletedSteps:
    def test_completed_steps_are_preserved(self) -> None:
        steps = _three_step_chain()
        steps[0] = _snapshot(
            A,
            order_index=0,
            status=RoadmapStepStatus.COMPLETED,
            completed_at="2026-09-25T00:00:00+00:00",
        )
        result = adapt(
            _roadmap(),
            steps,
            mastery={},
            velocity=0.5,
            reason="Weekly adaptation",
        )
        completed = [step for step in result.steps if step.status is RoadmapStepStatus.COMPLETED]
        assert len(completed) == 1
        assert completed[0].concept_id == A
        assert completed[0].completed_at is not None
        assert result.changed is True
        # B is unblocked by the completed prerequisite.
        by_concept = {step.concept_id: step for step in result.steps}
        assert by_concept[B].status is RoadmapStepStatus.AVAILABLE
        assert by_concept[B].blocked_by == ()

    def test_completed_step_is_not_dropped_even_when_mastered(self) -> None:
        steps = [
            _snapshot(A, order_index=0, status=RoadmapStepStatus.COMPLETED),
            _snapshot(B, order_index=1, status=RoadmapStepStatus.BLOCKED, blocked_by=(A,)),
        ]
        result = adapt(
            _roadmap(),
            steps,
            mastery={A: 1.0},
            velocity=1.0,
            reason="Mastery arrived",
        )
        assert any(step.concept_id == A for step in result.steps)
        assert result.dropped_concept_ids == ()


class TestMasteryAdaptation:
    def test_newly_mastered_prerequisite_is_dropped_and_unblocks_dependents(self) -> None:
        result = adapt(
            _roadmap(),
            _three_step_chain(),
            mastery={A: 0.95},
            velocity=0.5,
            reason="Linear algebra mastered",
        )
        assert result.changed is True
        assert result.dropped_concept_ids == (A,)
        assert result.revision == 2
        assert all(step.concept_id != A for step in result.steps)
        by_concept = {step.concept_id: step for step in result.steps}
        assert by_concept[B].status is RoadmapStepStatus.AVAILABLE
        assert by_concept[C].blocked_by == (B,)

    def test_newly_mastered_prerequisite_does_not_block(self) -> None:
        result = adapt(
            _roadmap(),
            _three_step_chain(),
            mastery={A: 0.7, B: 0.9},
            velocity=0.5,
            reason="Two prerequisites mastered",
        )
        assert set(result.dropped_concept_ids) == {A, B}
        remaining = [step for step in result.steps if step.concept_id == C]
        assert remaining[0].status is RoadmapStepStatus.AVAILABLE
        assert remaining[0].blocked_by == ()


class TestNoOp:
    def test_unchanged_adaptation_creates_no_revision(self) -> None:
        result = adapt(
            _roadmap(revision=3),
            _three_step_chain(),
            mastery={},
            velocity=None,
            reason="Nothing changed",
        )
        assert result.changed is False
        assert result.revision == 3
        assert result.dropped_concept_ids == ()
        assert result.inserted_concept_ids == ()
        # The recomputed statuses match the stored ones exactly.
        assert [step.status for step in result.steps] == [
            RoadmapStepStatus.AVAILABLE,
            RoadmapStepStatus.BLOCKED,
            RoadmapStepStatus.BLOCKED,
        ]

    def test_in_progress_is_preserved_when_still_available(self) -> None:
        steps = _three_step_chain()
        steps[0] = _snapshot(A, order_index=0, status=RoadmapStepStatus.IN_PROGRESS)
        result = adapt(_roadmap(), steps, mastery={}, velocity=None, reason="No change")
        assert result.steps[0].status is RoadmapStepStatus.IN_PROGRESS
        assert result.changed is False


class TestCandidates:
    def test_new_prerequisite_step_is_inserted_before_its_dependent(self) -> None:
        steps = [_snapshot(A, order_index=0, status=RoadmapStepStatus.AVAILABLE)]
        candidate = PlannedStep(
            concept_id=NEW,
            title="Category theory",
            description="Study Category Theory.",
            order_index=0,
            status=RoadmapStepStatus.PENDING,
            blocked_by=(A,),
            estimated_hours=3.0,
            prerequisites=(A,),
        )
        result = adapt(
            _roadmap(),
            steps,
            mastery={},
            velocity=0.5,
            reason="A new prerequisite surfaced",
            candidates=[candidate],
        )
        assert result.changed is True
        assert result.inserted_concept_ids == (NEW,)
        assert [step.concept_id for step in result.steps] == [A, NEW]
        assert result.steps[1].blocked_by == (A,)
        assert result.steps[1].status is RoadmapStepStatus.BLOCKED

    def test_mastered_candidate_is_not_inserted(self) -> None:
        steps = [_snapshot(A, order_index=0, status=RoadmapStepStatus.AVAILABLE)]
        candidate = PlannedStep(
            concept_id=NEW,
            title="Category theory",
            description="Study Category Theory.",
            order_index=0,
            status=RoadmapStepStatus.PENDING,
            blocked_by=(),
            estimated_hours=3.0,
            prerequisites=(),
        )
        result = adapt(
            _roadmap(),
            steps,
            mastery={NEW: 0.9},
            velocity=0.5,
            reason="Already known",
            candidates=[candidate],
        )
        assert result.inserted_concept_ids == ()
        assert result.changed is False


class TestRevisionAudit:
    def test_reason_is_recorded_on_a_change(self) -> None:
        result = adapt(
            _roadmap(revision=4),
            _three_step_chain(),
            mastery={A: 0.9},
            velocity=0.5,
            reason="Prerequisite mastered after quiz",
        )
        assert result.changed is True
        assert result.revision == 5
        assert result.reason == "Prerequisite mastered after quiz"

    def test_missing_velocity_is_recorded_as_degraded(self) -> None:
        result = adapt(_roadmap(), _three_step_chain(), mastery={}, velocity=None, reason="x")
        assert "velocity_unknown" in result.degraded
