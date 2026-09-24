"""Deterministic composition of "why this resource?".

The explanation is a *view of the score decomposition*, not a second opinion. It
names only the gaps that :class:`~coursellm.recommend.ranking.ScoredResource`
actually recorded as covered — the intersection of the resource's concept slugs
with the gaps — so it cannot claim a coverage the ranking did not compute. Every
sentence is a template over typed fields; there is no model call here at all.

That is a deliberate refusal, not an omission. Narration is where a
recommendation system usually starts inventing justifications ("this renowned
text is the canonical introduction to...") that the ranking never used. If a
model is ever allowed to touch this text it may only *rephrase* the fields below,
never add a claim, and the call must stay optional and off the default path.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from coursellm.recommend.ranking import GapSpec, ScoredResource
from coursellm.recommend.schemas import RecommendationExplanation


def _priority(gap: GapSpec, mastery: Mapping[uuid.UUID, float]) -> tuple[float, int, str]:
    """Weakest evidence first, then harder concept, then slug.

    A gap the student is failing matters more than one they nearly know, so
    mastery is the primary key; difficulty and slug make the order total.
    """
    return (float(mastery.get(gap.concept_id, 0.0)), -gap.difficulty, gap.slug)


def explain(
    score: ScoredResource,
    *,
    gaps: Sequence[GapSpec],
    mastery: Mapping[uuid.UUID, float],
) -> RecommendationExplanation:
    """Compose the explanation for one scored resource.

    ``gaps`` and ``mastery`` are the same inputs the ranking used, so the
    explanation is reproducible from the score plus those two arguments alone.
    When the score records no covered gap, the summary says exactly that and the
    primary-gap fields stay ``None`` — the function never names a gap the score
    does not contain.
    """
    by_slug = {gap.slug: gap for gap in gaps}
    covered = [by_slug[slug] for slug in score.covered_slugs if slug in by_slug]
    covered.sort(key=lambda gap: _priority(gap, mastery))
    covered_slugs = [gap.slug for gap in covered]
    covered_names = [gap.name for gap in covered]

    covered_set = set(covered_slugs)
    uncovered = sorted(
        (gap for gap in gaps if gap.slug not in covered_set),
        key=lambda gap: _priority(gap, mastery),
    )
    next_gap = uncovered[0] if uncovered else None
    primary = covered[0] if covered else None

    if not score.personalised:
        summary = (
            "No personalisation was possible: no knowledge gap was identified, "
            "so this is the catalogue's default order."
        )
    elif primary is None:
        summary = "This resource is not recorded as covering any identified knowledge gap."
    else:
        extras = covered_names[1:]
        if extras:
            summary = f"Closes the gap in {primary.name}. It also covers {', '.join(extras)}."
        else:
            summary = f"Closes the gap in {primary.name}."

    if next_gap is not None:
        next_step = f"Next, focus on {next_gap.name}."
    elif primary is not None:
        next_step = "This resource covers every identified gap."
    elif score.personalised:
        next_step = "No further gap was identified."
    else:
        next_step = "Attempt some practice questions so a gap can be identified."

    return RecommendationExplanation(
        resource_id=score.resource.id,
        summary=summary,
        primary_gap_slug=None if primary is None else primary.slug,
        primary_gap_name=None if primary is None else primary.name,
        covered_gap_slugs=covered_slugs,
        covered_gap_names=covered_names,
        next_gap_slug=None if next_gap is None else next_gap.slug,
        next_gap_name=None if next_gap is None else next_gap.name,
        next_step=next_step,
        coverage=score.contributions.coverage,
        contributions=score.contributions.as_dict(),
        personalised=score.personalised,
    )


__all__ = ["explain"]
