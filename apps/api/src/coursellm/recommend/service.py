"""The recommendation use case: gaps -> candidates -> rank -> explain.

The order of operations is the specification, and each step is deliberate:

1. **Resolve the gaps.** An explicit ``concept_ids`` list wins; otherwise the
   remaining steps of the caller's active roadmap; otherwise
   :meth:`~coursellm.graph.repository.ConceptGraphRepository.knowledge_gap`
   against the student's measured mastery. Mastery is a projection of the
   append-only evidence, never a stored score.
2. **Fetch candidates from the catalogue only.** A gap with no catalogue coverage
   is reported in ``degraded`` and left empty. It is never filled with a
   plausible-looking but unrelated resource — that is the failure mode this whole
   module is arranged to avoid.
3. **Rank** with the pure function in :mod:`coursellm.recommend.ranking`.
4. **Explain** each selected row from its score decomposition.

When there are no gaps at all the result is honest about it: ``degraded`` names
``no_gaps``, ``personalised`` is false, and the catalogue's default order (trust,
then title) is returned with explanations that say no personalisation happened.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.db.models.graph import Concept
from coursellm.db.models.learning import (
    Roadmap,
    RoadmapStatus,
    RoadmapStep,
    RoadmapStepStatus,
)
from coursellm.db.models.resource import Resource
from coursellm.db.tenancy import TenantScope
from coursellm.graph.repository import ConceptGraphRepository
from coursellm.learning.planner import load_mastery
from coursellm.learning.progress import DEFAULT_MASTERY_THRESHOLD, weak_concepts
from coursellm.recommend.catalogue import ResourceCandidate, ResourceCatalogue
from coursellm.recommend.explanation import explain
from coursellm.recommend.ranking import (
    NEUTRAL_DIFFICULTY_TARGET,
    GapSpec,
    ScoredResource,
    rank,
)
from coursellm.recommend.schemas import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    RecommendationExplanation,
)

#: Number of weak concepts whose prerequisite closures are inspected when there
#: is no roadmap to read. Bounded so one call cannot walk an unbounded graph.
MAX_GAP_TARGETS = 20
#: Rows fetched for the non-personalised catalogue listing.
NON_PERSONALISED_CATALOGUE_LIMIT = 200

#: Degradation markers. ``no_catalogue_coverage`` is emitted once, plus one
#: ``no_catalogue_coverage:<slug>`` entry per uncovered gap, so the caller can
#: both detect the condition and say which gap caused it.
DEGRADED_NO_GAPS = "no_gaps"
DEGRADED_NO_CATALOGUE_COVERAGE = "no_catalogue_coverage"
DEGRADED_NO_PROGRESS_EVIDENCE = "no_progress_evidence"
DEGRADED_DIFFICULTY_FILTER = "difficulty_filter_excluded_all"


@dataclass(frozen=True, slots=True)
class RecommendationGap:
    """A gap the recommendation set was built to close, with the student's state."""

    concept_id: uuid.UUID
    slug: str
    name: str
    difficulty: int
    mastery: float
    never_assessed: bool


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One recommended catalogue resource and why it was chosen."""

    resource: Resource
    score: ScoredResource
    explanation: RecommendationExplanation


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    """Ranked recommendations, the gaps they address, and how the call degraded."""

    recommendations: tuple[Recommendation, ...]
    gaps: tuple[RecommendationGap, ...]
    degraded: tuple[str, ...]
    personalised: bool


def _clip_difficulty(value: int) -> int:
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, value))


def _gap_from_concept(concept: Concept, mastery: dict[uuid.UUID, float]) -> RecommendationGap:
    return RecommendationGap(
        concept_id=concept.id,
        slug=concept.slug,
        name=concept.name,
        difficulty=concept.difficulty,
        mastery=float(mastery.get(concept.id, 0.0)),
        never_assessed=concept.id not in mastery,
    )


def _difficulty_target(
    gaps: Sequence[RecommendationGap],
    *,
    difficulty_max: int | None,
) -> int:
    """The difficulty the ranking should aim at.

    An explicit ``difficulty_max`` is used verbatim. Otherwise the target rises
    with the student's measured mastery of the gap concepts: a student with no
    evidence is aimed at the foundational end, and one who is nearly there is
    aimed at the advanced end. This is the whole of "difficulty fit against
    measured mastery".
    """
    if difficulty_max is not None:
        return _clip_difficulty(difficulty_max)
    if not gaps:
        return NEUTRAL_DIFFICULTY_TARGET
    mean_mastery = sum(gap.mastery for gap in gaps) / len(gaps)
    span = MAX_DIFFICULTY - MIN_DIFFICULTY
    return _clip_difficulty(MIN_DIFFICULTY + round(mean_mastery * span))


async def _active_roadmap(
    session: AsyncSession,
    scope: TenantScope,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
) -> Roadmap | None:
    statement = (
        select(Roadmap)
        .where(
            Roadmap.tenant_id == scope.tenant_id,
            Roadmap.user_id == user_id,
            Roadmap.status == RoadmapStatus.ACTIVE,
        )
        .order_by(Roadmap.created_at.desc(), Roadmap.id)
        .limit(1)
    )
    if course_id is not None:
        statement = statement.where(Roadmap.course_id == course_id)
    return (await session.execute(statement)).scalars().first()


async def _remaining_steps(
    session: AsyncSession, scope: TenantScope, roadmap_id: uuid.UUID
) -> list[RoadmapStep]:
    statement = (
        select(RoadmapStep)
        .where(
            RoadmapStep.tenant_id == scope.tenant_id,
            RoadmapStep.roadmap_id == roadmap_id,
            RoadmapStep.status != RoadmapStepStatus.COMPLETED,
        )
        .order_by(RoadmapStep.order_index, RoadmapStep.id)
    )
    return list((await session.execute(statement)).scalars().all())


async def _resolve_gaps(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    concept_ids: Sequence[uuid.UUID] | None,
    mastery: dict[uuid.UUID, float],
    degraded: list[str],
) -> list[RecommendationGap]:
    """Resolve the gap set by the documented precedence."""
    repository = ConceptGraphRepository(session, scope)

    if concept_ids:
        resolved: list[RecommendationGap] = []
        seen: set[uuid.UUID] = set()
        for concept_id in concept_ids:
            concept = await repository.get(concept_id)
            if concept is None:
                degraded.append(f"unknown_concept:{concept_id}")
                continue
            if concept.id in seen:
                continue
            seen.add(concept.id)
            resolved.append(_gap_from_concept(concept, mastery))
        return resolved

    roadmap = await _active_roadmap(session, scope, user_id=user_id, course_id=course_id)
    if roadmap is not None:
        steps = await _remaining_steps(session, scope, roadmap.id)
        roadmap_gaps: list[RecommendationGap] = []
        seen_steps: set[uuid.UUID] = set()
        for step in steps:
            if step.concept_id is None or step.concept_id in seen_steps:
                continue
            concept = await repository.get(step.concept_id)
            if concept is None:
                continue
            seen_steps.add(concept.id)
            roadmap_gaps.append(_gap_from_concept(concept, mastery))
        if roadmap_gaps:
            return roadmap_gaps

    weak = weak_concepts(mastery, DEFAULT_MASTERY_THRESHOLD)[:MAX_GAP_TARGETS]
    if not weak:
        if not mastery:
            degraded.append(DEGRADED_NO_PROGRESS_EVIDENCE)
        return []

    discovered: dict[uuid.UUID, RecommendationGap] = {}
    for target in weak:
        concept = await repository.get(target)
        if concept is not None:
            discovered.setdefault(concept.id, _gap_from_concept(concept, mastery))
        for gap in await repository.knowledge_gap(
            {str(concept_id): value for concept_id, value in mastery.items()},
            target,
            max_depth=settings.graph_max_depth,
            min_confidence=settings.graph_min_traversable_confidence,
            mastery_threshold=DEFAULT_MASTERY_THRESHOLD,
        ):
            discovered.setdefault(
                gap.concept_id,
                RecommendationGap(
                    concept_id=gap.concept_id,
                    slug=gap.slug,
                    name=gap.name,
                    difficulty=gap.difficulty,
                    mastery=gap.mastery,
                    never_assessed=gap.never_assessed,
                ),
            )
    return sorted(discovered.values(), key=lambda gap: (gap.mastery, -gap.difficulty, gap.slug))


async def _non_personalised_candidates(catalogue: ResourceCatalogue) -> list[ResourceCandidate]:
    resources = await catalogue.all_resources(limit=NON_PERSONALISED_CATALOGUE_LIMIT)
    return [
        ResourceCandidate(
            resource=resource,
            covered_slugs=await catalogue.concept_slugs_for(resource.id),
        )
        for resource in resources
    ]


async def recommend_for_user(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    concept_ids: Sequence[uuid.UUID] | None = None,
    limit: int = 10,
    difficulty_max: int | None = None,
) -> RecommendationResult:
    """Rank the catalogue against the caller's gaps and explain every pick.

    ``limit`` bounds the returned rows only; the coverage check runs over the whole
    candidate set, so a gap is reported uncovered only when the catalogue truly
    has no resource for it.
    """
    now = datetime.now(UTC)
    degraded: list[str] = []
    mastery = await load_mastery(session, scope, user_id=user_id)
    gaps = await _resolve_gaps(
        session,
        scope,
        settings,
        user_id=user_id,
        course_id=course_id,
        concept_ids=concept_ids,
        mastery=mastery,
        degraded=degraded,
    )

    catalogue = ResourceCatalogue(session)
    if gaps:
        candidates = await catalogue.covering([gap.slug for gap in gaps])
        if difficulty_max is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.resource.difficulty <= difficulty_max
            ]
            if not candidates:
                degraded.append(DEGRADED_DIFFICULTY_FILTER)
    else:
        degraded.append(DEGRADED_NO_GAPS)
        candidates = await _non_personalised_candidates(catalogue)

    gap_specs = [
        GapSpec(
            concept_id=gap.concept_id,
            slug=gap.slug,
            name=gap.name,
            difficulty=gap.difficulty,
        )
        for gap in gaps
    ]
    target = _difficulty_target(gaps, difficulty_max=difficulty_max)
    scored = rank(
        candidates,
        gaps=gap_specs,
        mastery=mastery,
        difficulty_target=target,
        now=now,
    )
    selected = scored[: max(limit, 0)]

    if gaps:
        covered = {slug for candidate in candidates for slug in candidate.covered_slugs}
        uncovered = [gap for gap in gaps if gap.slug not in covered]
        if uncovered:
            degraded.append(DEGRADED_NO_CATALOGUE_COVERAGE)
            degraded.extend(
                f"{DEGRADED_NO_CATALOGUE_COVERAGE}:{gap.slug}"
                for gap in sorted(uncovered, key=lambda item: item.slug)
            )

    recommendations = tuple(
        Recommendation(
            resource=score.resource,
            score=score,
            explanation=explain(score, gaps=gap_specs, mastery=mastery),
        )
        for score in selected
    )
    return RecommendationResult(
        recommendations=recommendations,
        gaps=tuple(gaps),
        degraded=tuple(dict.fromkeys(degraded)),
        personalised=bool(gaps),
    )


__all__ = [
    "DEGRADED_DIFFICULTY_FILTER",
    "DEGRADED_NO_CATALOGUE_COVERAGE",
    "DEGRADED_NO_GAPS",
    "DEGRADED_NO_PROGRESS_EVIDENCE",
    "MAX_GAP_TARGETS",
    "NON_PERSONALISED_CATALOGUE_LIMIT",
    "Recommendation",
    "RecommendationGap",
    "RecommendationResult",
    "recommend_for_user",
]
