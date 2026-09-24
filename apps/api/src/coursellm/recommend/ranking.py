"""Pure, deterministic ranking of catalogue candidates against knowledge gaps.

The function in this module performs **no I/O** and reads no clock: everything it
needs — candidates, gaps, mastery, an explicit target and ``now`` — is an
argument. That is what makes the ranking unit-testable without a database and,
more importantly, what makes the same inputs always produce the same order.

The score is a weighted sum of four contributions, and every contribution is
returned on :class:`ScoredResource` so the recommendation can be explained
without re-deriving anything:

``score = W_COVERAGE * coverage + W_DIFFICULTY * difficulty_fit
          + W_TRUST * trust + W_RECENCY * recency_quality``

* **Coverage dominates** (0.55). A resource that closes more of the student's
  gaps must outrank a better-produced resource that closes fewer. Coverage is
  weighted by ``1 - mastery`` per gap, so a gap the student is nearly through
  counts for less than one they have never touched; the weights are floored at
  :data:`MIN_GAP_WEIGHT` so a fully-mastered gap still contributes, which keeps
  "covers more gaps" true even in degenerate mastery data.
* **Difficulty fit** (0.20) rewards a resource whose difficulty is close to a
  target derived from the student's measured mastery, so a beginner is not handed
  an expert monograph merely because it covers the gap.
* **Trust** (0.15) prefers official and academic sources over community ones.
* **Recency and quality** (0.10) is deliberately small: a newer, better-rated
  edition is a tie-breaker, never a reason to prefer a less relevant resource.
  An unknown year scores neutrally rather than as new, so an undated resource is
  not rewarded for missing metadata.

When there are no gaps the ranking cannot be personal, so it says so: each
returned row carries ``personalised=False`` and the order is the documented
non-personalised order — trust, then title, then URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from coursellm.db.models.resource import Resource
from coursellm.recommend.catalogue import ResourceCandidate
from coursellm.recommend.schemas import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    SourceTrust,
)

#: Weights of the four score contributions. They sum to 1.0, which keeps the
#: total score in ``[0, 1]`` and makes the vector readable at a glance.
SCORE_WEIGHT_COVERAGE = 0.55
SCORE_WEIGHT_DIFFICULTY = 0.20
SCORE_WEIGHT_TRUST = 0.15
SCORE_WEIGHT_RECENCY = 0.10

#: Trust levels as a rankable score. ``secondary`` is the level reserved for
#: pages fetched at request time, which this PR does not do.
TRUST_SCORES: dict[SourceTrust, float] = {
    SourceTrust.OFFICIAL: 1.0,
    SourceTrust.ACADEMIC: 0.8,
    SourceTrust.COMMUNITY: 0.5,
    SourceTrust.SECONDARY: 0.25,
}

#: A resource is "current" within this many years; older entries fall towards 0.
RECENCY_HORIZON_YEARS = 30
#: Score for a resource with no publication year. Neutral, not 1.0, so missing
#: metadata is never rewarded.
UNKNOWN_YEAR_RECENCY = 0.5
#: Rating contribution when no rating exists, on a 0-1 scale.
NEUTRAL_RATING = 0.5
#: Floor on a gap's weight so a fully-mastered gap still counts for coverage.
MIN_GAP_WEIGHT = 0.1
#: Difficulty target used when the caller does not supply one (mid-scale).
NEUTRAL_DIFFICULTY_TARGET = int((MIN_DIFFICULTY + MAX_DIFFICULTY) / 2)


@dataclass(frozen=True, slots=True)
class GapSpec:
    """One gap the ranking should try to close, without any student data.

    The concept's ``difficulty`` and the student's ``mastery`` live here and in
    the ``mastery`` argument respectively, so the ranking can be exercised in a
    unit test with hand-built values.
    """

    concept_id: uuid.UUID
    slug: str
    name: str
    difficulty: int


@dataclass(frozen=True, slots=True)
class ScoreContributions:
    """The four terms the total score is composed of, each already weighted."""

    coverage: float
    difficulty_fit: float
    trust: float
    recency_quality: float

    def as_dict(self) -> dict[str, float]:
        """A stable, serialisable view used by the explanation and the API."""
        return {
            "coverage": round(self.coverage, 6),
            "difficulty_fit": round(self.difficulty_fit, 6),
            "trust": round(self.trust, 6),
            "recency_quality": round(self.recency_quality, 6),
        }


@dataclass(frozen=True, slots=True)
class ScoredResource:
    """A candidate with its score, its decomposition and the gaps it covers."""

    resource: Resource
    score: float
    contributions: ScoreContributions
    covered_slugs: tuple[str, ...]
    personalised: bool


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _gap_weight(mastery: float) -> float:
    return max(MIN_GAP_WEIGHT, 1.0 - _clip01(mastery))


def coverage_for(
    covered_slugs: Sequence[str],
    *,
    gap_weights: Mapping[str, float],
    total_weight: float,
) -> float:
    """Mastery-weighted fraction of the gaps a resource covers, in ``[0, 1]``."""
    if total_weight <= 0.0:
        return 0.0
    covered_weight = sum(gap_weights[slug] for slug in covered_slugs if slug in gap_weights)
    return _clip01(covered_weight / total_weight)


def difficulty_fit_for(difficulty: int, target: int) -> float:
    """How close a resource's difficulty is to ``target``, in ``[0, 1]``."""
    span = max(MAX_DIFFICULTY - MIN_DIFFICULTY, 1)
    return _clip01(1.0 - abs(difficulty - target) / span)


def recency_quality_for(resource: Resource, now: datetime) -> float:
    """A small term combining publication recency and the (optional) rating."""
    if resource.year is None:
        recency = UNKNOWN_YEAR_RECENCY
    else:
        age = max(0, now.year - resource.year)
        recency = _clip01(1.0 - age / RECENCY_HORIZON_YEARS)
    rating = NEUTRAL_RATING if resource.rating is None else _clip01(float(resource.rating) / 5.0)
    return _clip01(0.5 * recency + 0.5 * rating)


def score_candidate(
    candidate: ResourceCandidate,
    *,
    gap_slugs: frozenset[str],
    gap_weights: Mapping[str, float],
    total_weight: float,
    difficulty_target: int,
    now: datetime,
    personalised: bool,
) -> ScoredResource:
    """Score one candidate, returning every contribution used to do it."""
    covered = tuple(sorted(slug for slug in candidate.covered_slugs if slug in gap_slugs))
    coverage = coverage_for(covered, gap_weights=gap_weights, total_weight=total_weight)
    difficulty_fit = difficulty_fit_for(candidate.resource.difficulty, difficulty_target)
    trust = TRUST_SCORES[candidate.resource.trust]
    recency_quality = recency_quality_for(candidate.resource, now)
    total = (
        SCORE_WEIGHT_COVERAGE * coverage
        + SCORE_WEIGHT_DIFFICULTY * difficulty_fit
        + SCORE_WEIGHT_TRUST * trust
        + SCORE_WEIGHT_RECENCY * recency_quality
    )
    return ScoredResource(
        resource=candidate.resource,
        score=round(_clip01(total), 6),
        contributions=ScoreContributions(
            coverage=coverage,
            difficulty_fit=difficulty_fit,
            trust=trust,
            recency_quality=recency_quality,
        ),
        covered_slugs=covered,
        personalised=personalised,
    )


def rank(
    candidates: Sequence[ResourceCandidate],
    *,
    gaps: Sequence[GapSpec],
    mastery: Mapping[uuid.UUID, float],
    difficulty_target: int | None,
    now: datetime,
) -> list[ScoredResource]:
    """Rank candidates against ``gaps``, deterministically.

    The tie-break is the URL, which is the catalogue's natural unique key, so the
    order is total and stable across runs and database probe orderings. With no
    gaps the ordering is trust, then title, then URL, and every row is marked
    ``personalised=False``: the function records that it could not personalise
    rather than presenting an arbitrary catalogue order as a recommendation.
    """
    gap_list = tuple(gaps)
    gap_slugs = frozenset(gap.slug for gap in gap_list)
    gap_weights = {gap.slug: _gap_weight(mastery.get(gap.concept_id, 0.0)) for gap in gap_list}
    total_weight = sum(gap_weights.values())
    target = difficulty_target if difficulty_target is not None else NEUTRAL_DIFFICULTY_TARGET
    personalised = bool(gap_list)

    scored = [
        score_candidate(
            candidate,
            gap_slugs=gap_slugs,
            gap_weights=gap_weights,
            total_weight=total_weight,
            difficulty_target=target,
            now=now,
            personalised=personalised,
        )
        for candidate in candidates
    ]

    if gap_list:
        scored.sort(key=lambda item: (-item.score, item.resource.url))
    else:
        scored.sort(
            key=lambda item: (
                -TRUST_SCORES[item.resource.trust],
                item.resource.title,
                item.resource.url,
            )
        )
    return scored


__all__ = [
    "MIN_GAP_WEIGHT",
    "NEUTRAL_DIFFICULTY_TARGET",
    "NEUTRAL_RATING",
    "RECENCY_HORIZON_YEARS",
    "SCORE_WEIGHT_COVERAGE",
    "SCORE_WEIGHT_DIFFICULTY",
    "SCORE_WEIGHT_RECENCY",
    "SCORE_WEIGHT_TRUST",
    "TRUST_SCORES",
    "UNKNOWN_YEAR_RECENCY",
    "GapSpec",
    "ScoreContributions",
    "ScoredResource",
    "coverage_for",
    "difficulty_fit_for",
    "rank",
    "recency_quality_for",
    "score_candidate",
]
