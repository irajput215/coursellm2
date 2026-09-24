"""Ranking is pure, deterministic and fully decomposable.

Every test builds resources and candidates by hand and calls
:func:`coursellm.recommend.ranking.rank` directly — no database, no clock, no
fixture magic. That is the point of keeping the ranking free of I/O: the ordering
rules are pinned by values a reader can check by eye.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from coursellm.db.models.resource import Resource
from coursellm.recommend.catalogue import ResourceCandidate
from coursellm.recommend.ranking import (
    MIN_GAP_WEIGHT,
    SCORE_WEIGHT_COVERAGE,
    SCORE_WEIGHT_DIFFICULTY,
    SCORE_WEIGHT_RECENCY,
    SCORE_WEIGHT_TRUST,
    TRUST_SCORES,
    GapSpec,
    rank,
)
from coursellm.recommend.schemas import ResourceType, SourceTrust

pytestmark = pytest.mark.unit

NOW = datetime(2024, 6, 1, tzinfo=UTC)
GAPS_NAMESPACE = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _concept_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(GAPS_NAMESPACE, slug)


def _gap(slug: str, *, difficulty: int = 3, name: str | None = None) -> GapSpec:
    return GapSpec(
        concept_id=_concept_id(slug),
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        difficulty=difficulty,
    )


def _resource(
    *,
    title: str,
    url: str,
    trust: SourceTrust = SourceTrust.ACADEMIC,
    difficulty: int = 3,
    year: int | None = 2020,
    rating: float | None = None,
    resource_type: ResourceType = ResourceType.PAPER,
) -> Resource:
    return Resource(
        id=uuid.uuid4(),
        title=title,
        authors=[],
        publisher=None,
        year=year,
        url=url,
        resource_type=resource_type,
        provider="Provider",
        trust=trust,
        difficulty=difficulty,
        description="A real description.",
        duration_hours=None,
        is_free=True,
        rating=rating,
        is_verified=False,
        verified_at=None,
        notes=None,
    )


def _candidate(resource: Resource, *slugs: str) -> ResourceCandidate:
    return ResourceCandidate(resource=resource, covered_slugs=tuple(slugs))


def test_weights_are_a_documented_partition() -> None:
    total = (
        SCORE_WEIGHT_COVERAGE + SCORE_WEIGHT_DIFFICULTY + SCORE_WEIGHT_TRUST + SCORE_WEIGHT_RECENCY
    )
    assert total == pytest.approx(1.0)
    assert (
        max(
            SCORE_WEIGHT_COVERAGE, SCORE_WEIGHT_DIFFICULTY, SCORE_WEIGHT_TRUST, SCORE_WEIGHT_RECENCY
        )
        == SCORE_WEIGHT_COVERAGE
    )


def test_covering_more_gaps_outranks_covering_fewer() -> None:
    gaps = [_gap("linear-algebra"), _gap("probability"), _gap("optimization")]
    broad = _candidate(
        _resource(title="Broad", url="https://example.org/broad"),
        "linear-algebra",
        "probability",
    )
    narrow = _candidate(_resource(title="Narrow", url="https://example.org/narrow"), "probability")

    ranked = rank([narrow, broad], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)

    assert [item.resource.title for item in ranked] == ["Broad", "Narrow"]
    assert ranked[0].contributions.coverage > ranked[1].contributions.coverage
    assert ranked[1].contributions.coverage == pytest.approx(1 / 3)


def test_mastery_weights_which_gap_matters() -> None:
    """A resource that closes a gap the student has never touched wins."""
    strong = _gap("strong")
    weak = _gap("weak")
    mastery = {strong.concept_id: 0.95, weak.concept_id: 0.0}
    closes_strong = _candidate(_resource(title="Strong", url="https://example.org/s"), "strong")
    closes_weak = _candidate(_resource(title="Weak", url="https://example.org/w"), "weak")

    ranked = rank(
        [closes_strong, closes_weak],
        gaps=[strong, weak],
        mastery=mastery,
        difficulty_target=3,
        now=NOW,
    )

    assert ranked[0].resource.title == "Weak"
    expected_weak = 1.0 / (1.0 + MIN_GAP_WEIGHT)
    assert ranked[0].contributions.coverage == pytest.approx(expected_weak)


def test_difficulty_fit_rewards_the_matched_resource_over_a_much_harder_one() -> None:
    gaps = [_gap("deep-learning")]
    fit = _candidate(
        _resource(title="Fit", url="https://example.org/fit", difficulty=2), "deep-learning"
    )
    hard = _candidate(
        _resource(title="Hard", url="https://example.org/hard", difficulty=5), "deep-learning"
    )

    ranked = rank([hard, fit], gaps=gaps, mastery={}, difficulty_target=2, now=NOW)

    assert ranked[0].resource.title == "Fit"
    assert ranked[0].contributions.difficulty_fit > ranked[1].contributions.difficulty_fit
    assert ranked[0].contributions.difficulty_fit == pytest.approx(1.0)


def test_trust_orders_equal_resources() -> None:
    gaps = [_gap("sql")]
    official = _candidate(
        _resource(
            title="Official",
            url="https://example.org/official",
            trust=SourceTrust.OFFICIAL,
        ),
        "sql",
    )
    community = _candidate(
        _resource(
            title="Community",
            url="https://example.org/community",
            trust=SourceTrust.COMMUNITY,
        ),
        "sql",
    )

    ranked = rank([community, official], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)

    assert [item.resource.title for item in ranked] == ["Official", "Community"]
    assert ranked[0].contributions.trust == TRUST_SCORES[SourceTrust.OFFICIAL]
    assert ranked[1].contributions.trust == TRUST_SCORES[SourceTrust.COMMUNITY]


def test_ranking_is_deterministic_and_breaks_ties_on_url() -> None:
    gaps = [_gap("python")]
    first = _candidate(_resource(title="Same", url="https://example.org/a"), "python")
    second = _candidate(_resource(title="Same", url="https://example.org/b"), "python")

    ranked_once = rank([second, first], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)
    ranked_twice = rank([first, second], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)

    urls = [item.resource.url for item in ranked_once]
    assert urls == ["https://example.org/a", "https://example.org/b"]
    assert urls == [item.resource.url for item in ranked_twice]
    assert ranked_once[0].score == ranked_once[1].score


def test_empty_gaps_return_the_documented_non_personalised_order() -> None:
    candidates = [
        _candidate(
            _resource(title="Zeta", url="https://example.org/z", trust=SourceTrust.COMMUNITY)
        ),
        _candidate(
            _resource(title="Alpha", url="https://example.org/a", trust=SourceTrust.ACADEMIC)
        ),
        _candidate(
            _resource(title="Beta", url="https://example.org/b", trust=SourceTrust.OFFICIAL)
        ),
    ]

    ranked = rank(candidates, gaps=[], mastery={}, difficulty_target=None, now=NOW)

    assert [item.resource.title for item in ranked] == ["Beta", "Alpha", "Zeta"]
    assert all(item.personalised is False for item in ranked)
    assert all(item.contributions.coverage == 0.0 for item in ranked)


def test_every_contribution_is_present_and_sums_to_the_score() -> None:
    gaps = [_gap("probability")]
    candidate = _candidate(
        _resource(
            title="Scored",
            url="https://example.org/scored",
            trust=SourceTrust.OFFICIAL,
            difficulty=3,
            year=2023,
            rating=4.0,
        ),
        "probability",
    )

    ranked = rank([candidate], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)
    score = ranked[0]
    contributions = score.contributions.as_dict()

    assert set(contributions) == {"coverage", "difficulty_fit", "trust", "recency_quality"}
    assert all(0.0 <= value <= 1.0 for value in contributions.values())
    assert score.score == pytest.approx(
        SCORE_WEIGHT_COVERAGE * score.contributions.coverage
        + SCORE_WEIGHT_DIFFICULTY * score.contributions.difficulty_fit
        + SCORE_WEIGHT_TRUST * score.contributions.trust
        + SCORE_WEIGHT_RECENCY * score.contributions.recency_quality
    )


def test_only_published_gaps_are_recorded_as_covered() -> None:
    gaps = [_gap("python")]
    candidate = _candidate(
        _resource(title="Overlap", url="https://example.org/overlap"),
        "python",
        "not-a-real-gap",
    )

    ranked = rank([candidate], gaps=gaps, mastery={}, difficulty_target=3, now=NOW)

    assert ranked[0].covered_slugs == ("python",)
