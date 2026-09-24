"""Request and response schemas for recommendations and the catalogue.

The shapes mirror the service dataclasses rather than the ORM directly, so the
HTTP contract is stable even if the storage model changes. Two choices are worth
stating:

* A recommendation carries its **score decomposition** and its
  **explanation** alongside the resource. A number with no derivation would be
  unauditable, and the decomposition is what the explanation was composed from.
* A catalogue resource exposes provenance (provider, publisher, year, trust) but
  not an audit trail. ``is_verified`` is included because it is a claim about the
  row that a client should be able to see is false rather than assume.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from coursellm.recommend.schemas import (
    MAX_DIFFICULTY,
    MIN_DIFFICULTY,
    RecommendationExplanation,
    ResourceType,
    SourceTrust,
)


class ResourceResponse(BaseModel):
    """One catalogue resource."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    authors: list[str]
    publisher: str | None
    year: int | None
    url: str
    resource_type: ResourceType
    provider: str
    trust: SourceTrust
    difficulty: int = Field(ge=MIN_DIFFICULTY, le=MAX_DIFFICULTY)
    description: str
    duration_hours: float | None
    is_free: bool
    rating: float | None
    is_verified: bool
    verified_at: datetime | None
    notes: str | None


class ResourceWithConceptsResponse(ResourceResponse):
    """A resource together with the concept slugs it covers."""

    concept_slugs: list[str]


class CataloguePageResponse(BaseModel):
    """A page of catalogue search results."""

    items: list[ResourceResponse]
    total: int
    limit: int
    offset: int


class RecommendationGapResponse(BaseModel):
    """One gap the recommendation set was built to close."""

    model_config = ConfigDict(from_attributes=True)

    concept_id: uuid.UUID
    slug: str
    name: str
    difficulty: int
    mastery: float
    never_assessed: bool


class RecommendationItemResponse(BaseModel):
    """A recommended resource, its score decomposition and its explanation."""

    resource: ResourceResponse
    score: float
    contributions: dict[str, float]
    covered_gap_slugs: list[str]
    personalised: bool
    explanation: RecommendationExplanation


class RecommendationListResponse(BaseModel):
    """Recommendations for the caller, with the gaps and degradation markers."""

    recommendations: list[RecommendationItemResponse]
    gaps: list[RecommendationGapResponse]
    degraded: list[str]
    personalised: bool


class SeedReportResponse(BaseModel):
    """What one catalogue seeding run did; ``skipped`` proves idempotency."""

    total: int
    inserted: int
    skipped: int
    concept_links_inserted: int


__all__ = [
    "CataloguePageResponse",
    "RecommendationGapResponse",
    "RecommendationItemResponse",
    "RecommendationListResponse",
    "ResourceResponse",
    "ResourceWithConceptsResponse",
    "SeedReportResponse",
]
