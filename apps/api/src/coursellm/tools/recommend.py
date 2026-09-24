"""``get_recommendations`` — rank the curated catalogue against knowledge gaps.

The handler is a thin adapter. It resolves nothing itself: scope comes from
:class:`~coursellm.tools.registry.ToolContext` (never from arguments), and the
work is the :func:`coursellm.recommend.service.recommend_for_user` use case, so
the tool, the HTTP endpoint and the recommendation agent all rank the catalogue
the same way. Every returned item is a ``resources`` row: a resource that is not
in the catalogue cannot be returned, which is the guarantee that this tool never
invents a book, a course or a URL.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.db.tenancy import TenantScope
from coursellm.recommend.service import recommend_for_user
from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

Difficulty = Literal["easy", "medium", "hard"]

#: The tool's coarse difficulty knob mapped to the 1-5 scale stored on a resource.
#: ``hard`` intentionally reaches the top of the scale; there is no "unlimited".
DIFFICULTY_MAX_BY_LABEL: dict[str, int] = {"easy": 2, "medium": 3, "hard": 5}


class GetRecommendationsArgs(BaseModel):
    """Arguments for catalogue ranking. Scope is injected, never passed."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    concept_ids: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=5, ge=1, le=20)
    difficulty_max: Difficulty | None = None


class Recommendation(BaseModel):
    """One catalogue resource matched to a gap, with its provenance."""

    resource_id: str
    title: str
    url: str | None = None
    source_type: str = "other"
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class RecommendationsResult(BaseModel):
    """Ranked recommendations, or an empty list with a degradation reason."""

    recommendations: list[Recommendation] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


def _parse_concept_ids(raw: list[str]) -> tuple[list[uuid.UUID], int]:
    """Parse concept ids, returning the valid ones and how many were malformed."""
    parsed: list[uuid.UUID] = []
    invalid = 0
    for value in raw:
        try:
            parsed.append(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            invalid += 1
    return parsed, invalid


async def get_recommendations(
    args: GetRecommendationsArgs, ctx: ToolContext
) -> RecommendationsResult:
    """Rank the catalogue against the caller's gaps, or degrade honestly.

    A tool call without a user or a database session has nothing to personalise
    against or read from, and returns an empty list with a named degradation
    rather than a plausible-looking non-answer.
    """
    if ctx.user_id is None:
        return RecommendationsResult(recommendations=[], degraded=["no_user_context"])
    if ctx.session is None:
        return RecommendationsResult(recommendations=[], degraded=["tool_unavailable"])

    concept_ids, invalid = _parse_concept_ids(args.concept_ids)
    difficulty_max = (
        None if args.difficulty_max is None else DIFFICULTY_MAX_BY_LABEL[args.difficulty_max]
    )
    result = await recommend_for_user(
        ctx.session,
        TenantScope(tenant_id=ctx.tenant_id),
        ctx.settings,
        user_id=ctx.user_id,
        course_id=args.course_id,
        concept_ids=concept_ids or None,
        limit=args.limit,
        difficulty_max=difficulty_max,
    )

    degraded = list(result.degraded)
    if invalid:
        degraded.append("invalid_concept_ids")

    recommendations = [
        Recommendation(
            resource_id=str(item.resource.id),
            title=item.resource.title,
            url=item.resource.url,
            source_type=item.resource.resource_type.value,
            coverage=item.score.contributions.coverage,
            match_reasons=[item.explanation.summary, item.explanation.next_step],
            provenance={
                "provider": item.resource.provider,
                "publisher": item.resource.publisher,
                "year": item.resource.year,
                "trust": item.resource.trust.value,
                "is_verified": item.resource.is_verified,
                "covered_concept_slugs": list(item.score.covered_slugs),
                "contributions": item.score.contributions.as_dict(),
            },
        )
        for item in result.recommendations
    ]
    return RecommendationsResult(recommendations=recommendations, degraded=degraded)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_recommendations",
            description=(
                "Rank curated catalogue resources against the caller's knowledge "
                "gaps by coverage, difficulty fit and freshness, with provenance."
            ),
            parameters=GetRecommendationsArgs,
            required_permissions=frozenset({Permission.RESOURCES_READ}),
            side_effects="none",
            timeout_ms=4000,
            handler=get_recommendations,
        )
    )


__all__ = [
    "DIFFICULTY_MAX_BY_LABEL",
    "GetRecommendationsArgs",
    "Recommendation",
    "RecommendationsResult",
    "get_recommendations",
    "register",
]
