"""The mastery projection and the velocity, weak and stale read models.

Mastery is **derived, never stored**. ``progress_events`` and ``quiz_attempts``
are an append-only log, and this module is the single, deterministic function
that turns that log into the number the API shows. Two consequences are
deliberate:

* **Absence of evidence is not evidence of absence.** An empty history projects
  to an *empty map*, not to mastery 0 for every concept. A concept the student
  has never touched is unknown, not known-to-be-weak, and collapsing the two
  would silently schedule everything.
* **The projection depends only on its inputs.** Recency is measured relative to
  the newest observation in the history rather than to the wall clock, so the
  same log always produces the same mastery map. "Have I practised this lately?"
  is a separate question, answered by :func:`stale_concepts`.

The formula is a documented, weighted combination of three readings of the
evidence, each decayed exponentially by recency:

``mastery = clip(0.5 * decay_weighted_mean + 0.3 * best + 0.2 * latest, 0, 1)``

where ``decay = 0.5 ** (age_days / 30)``. The mean resists a single lucky quiz;
``best`` stops one bad day from erasing real competence; ``latest`` keeps the
projection responsive to the most recent evidence.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from coursellm.db.models.learning import ProgressEvent, ProgressEventKind, QuizAttempt

#: Recency half-life for mastery decay, in days.
MASTERY_HALF_LIFE_DAYS = 30.0
#: Default weights of the three readings combined into the projection. They sum
#: to 1.0. The application reads them from :class:`~coursellm.core.config.Settings`
#: (``MASTERY_*_WEIGHT``); these literals are the pure-function defaults.
MASTERY_MEAN_WEIGHT = 0.5
MASTERY_BEST_WEIGHT = 0.3
MASTERY_LATEST_WEIGHT = 0.2
#: Default mastery at or above which a concept is treated as known.
DEFAULT_MASTERY_THRESHOLD = 0.6
#: Velocity window and its minimum evidence rule.
VELOCITY_WINDOW_DAYS = 28.0
VELOCITY_MIN_MASTERED_CONCEPTS = 2
VELOCITY_MASTERY_THRESHOLD = 0.6
#: Default "not practised lately" horizon, in days.
DEFAULT_STALE_DAYS = 30.0

_WEIGHT_SUM_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class MasteryWeights:
    """The weights of the three readings combined into the projection.

    Frozen and validated so a drifted set fails where it is constructed rather
    than silently rescaling every mastery value.
    """

    mean: float = MASTERY_MEAN_WEIGHT
    best: float = MASTERY_BEST_WEIGHT
    latest: float = MASTERY_LATEST_WEIGHT

    def __post_init__(self) -> None:
        total = self.mean + self.best + self.latest
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            msg = f"The mastery weights must sum to 1.0; got {total!r}."
            raise ValueError(msg)


DEFAULT_MASTERY_WEIGHTS = MasteryWeights()


def mastery_weights(settings: Any) -> MasteryWeights:
    """The mastery weights from configuration."""
    return MasteryWeights(
        mean=settings.mastery_mean_weight,
        best=settings.mastery_best_weight,
        latest=settings.mastery_latest_weight,
    )


@dataclass(frozen=True, slots=True)
class _Observation:
    """One piece of evidence, normalised to (value, weight, time)."""

    concept_id: uuid.UUID
    value: float
    weight: float
    occurred_at: datetime


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _as_utc(value: Any) -> datetime | None:
    """Coerce a stored timestamp to an aware UTC datetime.

    SQLAlchemy returns aware datetimes for ``timestamptz``, but a test or a
    fixture may hand over a naive one; treating a naive value as UTC keeps the
    projection total rather than raising on a mixed history.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decay(occurred_at: datetime, reference: datetime) -> float:
    age_days = max(0.0, (reference - occurred_at).total_seconds() / 86_400.0)
    return float(0.5 ** (age_days / MASTERY_HALF_LIFE_DAYS))


def _observations(
    events: Sequence[ProgressEvent], attempts: Sequence[QuizAttempt]
) -> list[_Observation]:
    collected: list[_Observation] = []
    for event in events:
        concept_id = _as_uuid(event.concept_id)
        occurred_at = _as_utc(event.occurred_at)
        if concept_id is None or occurred_at is None:
            continue
        weight = max(float(event.weight or 0.0), 0.0)
        if weight <= 0.0:
            continue
        collected.append(
            _Observation(
                concept_id=concept_id,
                value=_clip01(float(event.mastery or 0.0)),
                weight=weight,
                occurred_at=occurred_at,
            )
        )
    for attempt in attempts:
        occurred_at = _as_utc(attempt.created_at)
        if occurred_at is None:
            continue
        value = _clip01(float(attempt.score or 0.0))
        for raw in attempt.concept_ids or []:
            concept_id = _as_uuid(raw)
            if concept_id is None:
                continue
            collected.append(
                _Observation(
                    concept_id=concept_id,
                    value=value,
                    weight=1.0,
                    occurred_at=occurred_at,
                )
            )
    return collected


def project_mastery(
    events: Sequence[ProgressEvent],
    attempts: Sequence[QuizAttempt],
    *,
    weights: MasteryWeights = DEFAULT_MASTERY_WEIGHTS,
) -> dict[uuid.UUID, float]:
    """Project per-concept mastery in ``[0, 1]`` from the append-only log.

    Deterministic given the same inputs, and an empty history yields an empty
    map: no evidence means no key, never a fabricated zero.
    """
    observations = _observations(events, attempts)
    if not observations:
        return {}

    reference = max(observation.occurred_at for observation in observations)
    grouped: dict[uuid.UUID, list[_Observation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.concept_id].append(observation)

    mastery: dict[uuid.UUID, float] = {}
    for concept_id, concept_observations in grouped.items():
        decayed = [
            (
                observation.value,
                observation.weight,
                _decay(observation.occurred_at, reference),
                observation.occurred_at,
            )
            for observation in concept_observations
        ]
        total_weight = sum(weight * decay for _, weight, decay, _ in decayed)
        if total_weight <= 0.0:
            continue
        mean = sum(value * weight * decay for value, weight, decay, _ in decayed) / total_weight
        best = max(value * decay for value, _, decay, _ in decayed)
        most_recent = max(decayed, key=lambda item: item[3])
        latest = most_recent[0] * most_recent[2]
        projected = weights.mean * mean + weights.best * best + weights.latest * latest
        mastery[concept_id] = round(_clip01(projected), 6)
    return mastery


def learning_velocity(events: Sequence[ProgressEvent], now: datetime) -> float | None:
    """Distinct concepts mastered per week over the trailing window.

    Returns ``None`` below :data:`VELOCITY_MIN_MASTERED_CONCEPTS` qualifying
    events: one mastered concept is a data point, not a rate, and a fabricated
    number would drive scheduling decisions that the evidence does not support.
    """
    current = _as_utc(now)
    if current is None:
        return None
    window_start = current - timedelta(days=VELOCITY_WINDOW_DAYS)
    mastered: set[uuid.UUID] = set()
    for event in events:
        if str(event.kind) != ProgressEventKind.CONCEPT_MASTERED.value:
            continue
        if float(event.mastery or 0.0) < VELOCITY_MASTERY_THRESHOLD:
            continue
        concept_id = _as_uuid(event.concept_id)
        occurred_at = _as_utc(event.occurred_at)
        if concept_id is None or occurred_at is None:
            continue
        if occurred_at < window_start or occurred_at > current:
            continue
        mastered.add(concept_id)
    if len(mastered) < VELOCITY_MIN_MASTERED_CONCEPTS:
        return None
    return len(mastered) / (VELOCITY_WINDOW_DAYS / 7.0)


def weak_concepts(
    mastery: Mapping[uuid.UUID, float],
    threshold: float = DEFAULT_MASTERY_THRESHOLD,
) -> list[uuid.UUID]:
    """Concept ids with mastery below ``threshold``, weakest and most stable first."""
    return sorted(
        (concept_id for concept_id, value in mastery.items() if float(value) < threshold),
        key=lambda concept_id: (float(mastery[concept_id]), str(concept_id)),
    )


def stale_concepts(
    mastery: Mapping[uuid.UUID, float],
    last_seen: Mapping[uuid.UUID, datetime],
    now: datetime,
    stale_days: float = DEFAULT_STALE_DAYS,
) -> list[uuid.UUID]:
    """Concepts not practised within ``stale_days``, oldest evidence first.

    Only concepts present in both maps are considered: a concept with no mastery
    evidence is *unknown*, which the gap detector handles, not *stale*.
    """
    current = _as_utc(now)
    if current is None:
        return []
    cutoff = current - timedelta(days=stale_days)
    seen: list[tuple[uuid.UUID, datetime]] = []
    for concept_id, value in last_seen.items():
        if concept_id not in mastery:
            continue
        when = _as_utc(value)
        if when is None or when >= cutoff:
            continue
        seen.append((concept_id, when))
    seen.sort(key=lambda item: (item[1], str(item[0])))
    return [concept_id for concept_id, _ in seen]


def last_seen_map(
    events: Sequence[ProgressEvent], attempts: Sequence[QuizAttempt]
) -> dict[uuid.UUID, datetime]:
    """The most recent evidence timestamp per concept."""
    latest: dict[uuid.UUID, datetime] = {}
    for observation in _observations(events, attempts):
        previous = latest.get(observation.concept_id)
        if previous is None or observation.occurred_at > previous:
            latest[observation.concept_id] = observation.occurred_at
    return latest


def attempt_counts(attempts: Sequence[QuizAttempt]) -> dict[uuid.UUID, int]:
    """How many quiz attempts touched each concept."""
    counts: dict[uuid.UUID, int] = defaultdict(int)
    for attempt in attempts:
        for raw in attempt.concept_ids or []:
            concept_id = _as_uuid(raw)
            if concept_id is not None:
                counts[concept_id] += 1
    return dict(counts)


__all__ = [
    "DEFAULT_MASTERY_THRESHOLD",
    "DEFAULT_MASTERY_WEIGHTS",
    "DEFAULT_STALE_DAYS",
    "MASTERY_BEST_WEIGHT",
    "MASTERY_HALF_LIFE_DAYS",
    "MASTERY_LATEST_WEIGHT",
    "MASTERY_MEAN_WEIGHT",
    "VELOCITY_MASTERY_THRESHOLD",
    "VELOCITY_MIN_MASTERED_CONCEPTS",
    "VELOCITY_WINDOW_DAYS",
    "MasteryWeights",
    "attempt_counts",
    "last_seen_map",
    "learning_velocity",
    "mastery_weights",
    "project_mastery",
    "stale_concepts",
    "weak_concepts",
]
