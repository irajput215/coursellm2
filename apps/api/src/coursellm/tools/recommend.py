"""``get_recommendations`` — rank catalogue resources against knowledge gaps.

The ``resources`` catalogue is PR 12 and has no table in this schema revision, so
the handler returns an empty typed list with
``degraded=["tool_unavailable"]``. Every recommendation is specified to carry
provenance back to a catalogue row; returning nothing is the only honest option
until that row exists.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

Difficulty = Literal["easy", "medium", "hard"]


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


async def get_recommendations(
    args: GetRecommendationsArgs, ctx: ToolContext
) -> RecommendationsResult:
    """Return an empty catalogue ranking; the catalogue lands in PR 12."""
    return RecommendationsResult(recommendations=[], degraded=["tool_unavailable"])


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
    "GetRecommendationsArgs",
    "Recommendation",
    "RecommendationsResult",
    "get_recommendations",
    "register",
]
