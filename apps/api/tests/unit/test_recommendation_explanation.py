"""The explanation is derived from the score, never invented.

These tests build a :class:`~coursellm.recommend.ranking.ScoredResource` with the
exact covered slugs they intend, then assert the composition says only what those
slugs support. The load-bearing case is the one where the score covers *nothing*:
the explanation must say so rather than reaching for a plausible-sounding gap.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from coursellm.db.models.resource import Resource
from coursellm.recommend.explanation import explain
from coursellm.recommend.ranking import (
    SCORE_WEIGHT_COVERAGE,
    SCORE_WEIGHT_DIFFICULTY,
    SCORE_WEIGHT_RECENCY,
    SCORE_WEIGHT_TRUST,
    GapSpec,
    ScoreContributions,
    ScoredResource,
)
from coursellm.recommend.schemas import ResourceType, SourceTrust

pytestmark = pytest.mark.unit

GAPS_NAMESPACE = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _concept_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(GAPS_NAMESPACE, slug)


def _gap(slug: str, *, difficulty: int = 3) -> GapSpec:
    return GapSpec(
        concept_id=_concept_id(slug),
        slug=slug,
        name=slug.replace("-", " ").title(),
        difficulty=difficulty,
    )


def _resource(title: str = "Resource") -> Resource:
    return Resource(
        id=uuid.uuid4(),
        title=title,
        authors=[],
        publisher=None,
        year=2021,
        url="https://example.org/resource",
        resource_type=ResourceType.PAPER,
        provider="Provider",
        trust=SourceTrust.ACADEMIC,
        difficulty=3,
        description="A real description.",
        duration_hours=None,
        is_free=True,
        rating=None,
        is_verified=False,
        verified_at=None,
        notes=None,
    )


def _scored(
    resource: Resource,
    *,
    covered_slugs: tuple[str, ...] = (),
    personalised: bool = True,
    coverage: float = 0.5,
) -> ScoredResource:
    contributions = ScoreContributions(
        coverage=coverage,
        difficulty_fit=0.5,
        trust=0.8,
        recency_quality=0.5,
    )
    score = (
        SCORE_WEIGHT_COVERAGE * contributions.coverage
        + SCORE_WEIGHT_DIFFICULTY * contributions.difficulty_fit
        + SCORE_WEIGHT_TRUST * contributions.trust
        + SCORE_WEIGHT_RECENCY * contributions.recency_quality
    )
    return ScoredResource(
        resource=resource,
        score=score,
        contributions=contributions,
        covered_slugs=covered_slugs,
        personalised=personalised,
    )


def test_explanation_names_the_gap_the_resource_covers() -> None:
    gaps = [_gap("linear-algebra"), _gap("probability")]
    score = _scored(_resource(), covered_slugs=("linear-algebra",), coverage=0.5)

    explanation = explain(score, gaps=gaps, mastery={})

    assert explanation.primary_gap_slug == "linear-algebra"
    assert explanation.primary_gap_name == "Linear Algebra"
    assert "Linear Algebra" in explanation.summary
    assert explanation.covered_gap_slugs == ["linear-algebra"]
    assert explanation.coverage == pytest.approx(0.5)
    assert explanation.personalised is True


def test_explanation_never_claims_coverage_the_score_lacks() -> None:
    gaps = [_gap("linear-algebra"), _gap("probability")]
    score = _scored(_resource(), covered_slugs=(), coverage=0.0)

    explanation = explain(score, gaps=gaps, mastery={})

    assert explanation.primary_gap_slug is None
    assert explanation.primary_gap_name is None
    assert explanation.covered_gap_slugs == []
    for gap in gaps:
        assert gap.name not in explanation.summary
    # It still says what to do next, which is a different kind of sentence.
    assert explanation.next_gap_slug in {"linear-algebra", "probability"}
    assert explanation.next_gap_name is not None
    assert explanation.next_gap_name in explanation.next_step


def test_explanation_is_deterministic() -> None:
    gaps = [_gap("probability"), _gap("linear-algebra")]
    score = _scored(_resource(), covered_slugs=("probability", "linear-algebra"))

    first = explain(score, gaps=gaps, mastery={})
    second = explain(score, gaps=gaps, mastery={})

    assert first == second


def test_weakest_gap_is_the_primary_gap() -> None:
    strong = _gap("probability")
    weak = _gap("linear-algebra")
    mastery = {strong.concept_id: 0.9, weak.concept_id: 0.1}
    score = _scored(
        _resource(),
        covered_slugs=("probability", "linear-algebra"),
    )

    explanation = explain(score, gaps=[strong, weak], mastery=mastery)

    assert explanation.primary_gap_slug == "linear-algebra"
    assert explanation.covered_gap_slugs == ["linear-algebra", "probability"]


def test_non_personalised_explanation_says_so() -> None:
    gaps = [_gap("probability")]
    score = _scored(_resource(), covered_slugs=(), personalised=False, coverage=0.0)

    explanation = explain(score, gaps=gaps, mastery={})

    assert explanation.personalised is False
    assert explanation.primary_gap_slug is None
    assert "personalisation" in explanation.summary


def test_next_step_names_the_gap_that_remains() -> None:
    gaps = [_gap("linear-algebra"), _gap("probability")]
    score = _scored(_resource(), covered_slugs=("linear-algebra",), coverage=0.5)

    explanation = explain(score, gaps=gaps, mastery={})

    assert explanation.next_gap_slug == "probability"
    assert explanation.next_gap_name == "Probability"
    assert "Probability" in explanation.next_step


def test_contributions_are_copied_from_the_score() -> None:
    gaps = [_gap("probability")]
    score = _scored(_resource(), covered_slugs=("probability",), coverage=0.75)

    explanation = explain(score, gaps=gaps, mastery={})

    assert explanation.contributions == score.contributions.as_dict()
    assert set(explanation.contributions) == {
        "coverage",
        "difficulty_fit",
        "trust",
        "recency_quality",
    }
