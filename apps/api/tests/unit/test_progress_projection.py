"""Unit tests for the mastery projection and the progress read models.

Every expectation is hand-computed from the documented formula, and the empty
history is asserted explicitly: absence of evidence must never become mastery 0.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from coursellm.db.models.learning import ProgressEvent, ProgressEventKind, QuizAttempt
from coursellm.learning.progress import (
    MASTERY_BEST_WEIGHT,
    MASTERY_HALF_LIFE_DAYS,
    MASTERY_LATEST_WEIGHT,
    MASTERY_MEAN_WEIGHT,
    attempt_counts,
    last_seen_map,
    learning_velocity,
    project_mastery,
    stale_concepts,
    weak_concepts,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _event(
    concept_id: uuid.UUID | None,
    *,
    mastery: float,
    occurred_at: datetime,
    weight: float = 1.0,
    kind: ProgressEventKind = ProgressEventKind.QUIZ_ATTEMPT,
) -> ProgressEvent:
    return ProgressEvent(
        user_id=uuid.uuid4(),
        concept_id=concept_id,
        kind=kind,
        mastery=mastery,
        weight=weight,
        occurred_at=occurred_at,
        source="test",
    )


def _attempt(
    concept_ids: list[uuid.UUID],
    *,
    score: float,
    created_at: datetime,
) -> QuizAttempt:
    return QuizAttempt(
        user_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        item_id="item-1",
        concept_ids=[str(concept_id) for concept_id in concept_ids],
        score=score,
        created_at=created_at,
    )


class TestProjectMastery:
    def test_hand_computed_projection(self) -> None:
        concept = uuid.uuid4()
        older = NOW - timedelta(days=MASTERY_HALF_LIFE_DAYS)
        events = [
            _event(concept, mastery=0.4, occurred_at=NOW),
            _event(concept, mastery=1.0, occurred_at=older),
        ]
        mastery = project_mastery(events, [])
        # decay(latest)=1.0, decay(older)=0.5.
        # mean = (0.4*1 + 1.0*0.5) / (1 + 0.5) = 0.6
        # best = max(0.4, 0.5) = 0.5, latest = 0.4
        # 0.5*0.6 + 0.3*0.5 + 0.2*0.4 = 0.53
        expected = (
            MASTERY_MEAN_WEIGHT * 0.6 + MASTERY_BEST_WEIGHT * 0.5 + MASTERY_LATEST_WEIGHT * 0.4
        )
        assert mastery == {concept: pytest.approx(round(expected, 6))}

    def test_recency_decay_direction(self) -> None:
        recent, stale = uuid.uuid4(), uuid.uuid4()
        events = [
            _event(recent, mastery=1.0, occurred_at=NOW),
            _event(stale, mastery=1.0, occurred_at=NOW - timedelta(days=90)),
        ]
        mastery = project_mastery(events, [])
        assert mastery[recent] > mastery[stale]
        assert mastery[recent] == pytest.approx(1.0)

    def test_quiz_attempts_contribute_scores(self) -> None:
        concept = uuid.uuid4()
        attempts = [_attempt([concept], score=0.8, created_at=NOW)]
        mastery = project_mastery([], attempts)
        # A single perfect-recency observation projects to its own score.
        assert mastery == {concept: pytest.approx(0.8)}

    def test_empty_history_is_an_empty_map(self) -> None:
        assert project_mastery([], []) == {}

    def test_events_without_a_concept_are_ignored(self) -> None:
        assert project_mastery([_event(None, mastery=1.0, occurred_at=NOW)], []) == {}

    def test_zero_weight_evidence_is_ignored(self) -> None:
        concept = uuid.uuid4()
        events = [_event(concept, mastery=1.0, occurred_at=NOW, weight=0.0)]
        assert project_mastery(events, []) == {}

    def test_projection_is_deterministic(self) -> None:
        concept = uuid.uuid4()
        events = [
            _event(concept, mastery=0.3, occurred_at=NOW),
            _event(concept, mastery=0.9, occurred_at=NOW - timedelta(days=7)),
        ]
        assert project_mastery(events, []) == project_mastery(events, [])

    def test_mastery_is_clipped_to_the_unit_interval(self) -> None:
        concept = uuid.uuid4()
        events = [
            _event(concept, mastery=1.0, occurred_at=NOW),
            _event(concept, mastery=1.0, occurred_at=NOW),
        ]
        assert project_mastery(events, [])[concept] <= 1.0


class TestLearningVelocity:
    def test_none_below_the_minimum_evidence(self) -> None:
        concept = uuid.uuid4()
        events = [
            _event(
                concept,
                mastery=0.9,
                occurred_at=NOW - timedelta(days=1),
                kind=ProgressEventKind.CONCEPT_MASTERED,
            )
        ]
        assert learning_velocity(events, NOW) is None

    def test_two_mastered_concepts_over_four_weeks(self) -> None:
        events = [
            _event(
                uuid.uuid4(),
                mastery=0.9,
                occurred_at=NOW - timedelta(days=1),
                kind=ProgressEventKind.CONCEPT_MASTERED,
            ),
            _event(
                uuid.uuid4(),
                mastery=0.8,
                occurred_at=NOW - timedelta(days=2),
                kind=ProgressEventKind.CONCEPT_MASTERED,
            ),
        ]
        assert learning_velocity(events, NOW) == pytest.approx(2 / 4.0)

    def test_old_and_unmastered_events_do_not_count(self) -> None:
        events = [
            _event(
                uuid.uuid4(),
                mastery=0.9,
                occurred_at=NOW - timedelta(days=90),
                kind=ProgressEventKind.CONCEPT_MASTERED,
            ),
            _event(
                uuid.uuid4(),
                mastery=0.2,
                occurred_at=NOW - timedelta(days=1),
                kind=ProgressEventKind.CONCEPT_MASTERED,
            ),
            _event(
                uuid.uuid4(),
                mastery=0.9,
                occurred_at=NOW - timedelta(days=1),
                kind=ProgressEventKind.TOPIC_COMPLETED,
            ),
        ]
        assert learning_velocity(events, NOW) is None

    def test_no_events_is_none(self) -> None:
        assert learning_velocity([], NOW) is None


class TestWeakAndStale:
    def test_weak_concepts_sorted_weakest_first(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mastery = {a: 0.2, b: 0.8, c: 0.5}
        assert weak_concepts(mastery, threshold=0.6) == [a, c]

    def test_no_weak_concepts_is_empty(self) -> None:
        assert weak_concepts({uuid.uuid4(): 0.9}, threshold=0.6) == []

    def test_stale_concepts_respects_the_horizon_and_order(self) -> None:
        old, ancient, fresh = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mastery = {old: 0.5, ancient: 0.5, fresh: 0.5}
        last_seen = {
            old: NOW - timedelta(days=40),
            ancient: NOW - timedelta(days=100),
            fresh: NOW - timedelta(days=1),
        }
        assert stale_concepts(mastery, last_seen, NOW, stale_days=30) == [ancient, old]

    def test_concepts_without_mastery_evidence_are_not_stale(self) -> None:
        unknown = uuid.uuid4()
        assert stale_concepts({}, {unknown: NOW - timedelta(days=400)}, NOW) == []


class TestHelpers:
    def test_last_seen_map_picks_the_most_recent_observation(self) -> None:
        concept = uuid.uuid4()
        events = [
            _event(concept, mastery=0.2, occurred_at=NOW - timedelta(days=10)),
            _event(concept, mastery=0.5, occurred_at=NOW),
        ]
        assert last_seen_map(events, []) == {concept: NOW}

    def test_last_seen_map_includes_attempts(self) -> None:
        concept = uuid.uuid4()
        attempts = [_attempt([concept], score=0.7, created_at=NOW - timedelta(days=1))]
        assert last_seen_map([], attempts) == {concept: NOW - timedelta(days=1)}

    def test_attempt_counts_per_concept(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        attempts = [
            _attempt([a, b], score=0.5, created_at=NOW),
            _attempt([a], score=0.6, created_at=NOW),
        ]
        assert attempt_counts(attempts) == {a: 2, b: 1}
