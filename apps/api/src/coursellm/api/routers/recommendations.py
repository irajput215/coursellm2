"""Recommendation and catalogue endpoints.

Four routes, one of which writes:

* ``GET /recommendations`` ranks the catalogue against the caller's gaps. Gaps
  come from the authenticated caller's own roadmap or mastery evidence, so two
  users in the same tenant cannot see one another's gaps.
* ``GET /recommendations/resources`` searches the public catalogue, paginated.
  It is declared before ``/{resource_id}`` so the literal segment wins.
* ``GET /recommendations/{resource_id}`` returns one resource and its coverage.
* ``POST /recommendations/seed`` is the only write, and it writes **global**
  data: the curated catalogue is shared by every tenant. The role check is
  therefore load-bearing, not decorative — a member must not be able to mutate
  what every other tenant reads. It is enforced by
  :func:`coursellm.api.deps.require_role`, and a security/integration test
  asserts the 403 for a member and the 200 for an owner.

A request without a valid bearer token is a 401 on every route, including the
catalogue search: the catalogue is public learning material, but the API surface
is still authenticated so that usage is attributable.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from coursellm.api.deps import (
    ContextDep,
    SettingsDep,
    TenantSessionDep,
    require_role,
)
from coursellm.api.schemas.recommendation import (
    CataloguePageResponse,
    RecommendationGapResponse,
    RecommendationItemResponse,
    RecommendationListResponse,
    ResourceResponse,
    ResourceWithConceptsResponse,
    SeedReportResponse,
)
from coursellm.core.errors import NotFoundError
from coursellm.db.models.identity import UserRole
from coursellm.recommend.catalogue import ResourceCatalogue
from coursellm.recommend.schemas import ResourceType, SourceTrust
from coursellm.recommend.seed import seed_catalogue
from coursellm.recommend.service import RecommendationResult, recommend_for_user

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _list_response(result: RecommendationResult) -> RecommendationListResponse:
    return RecommendationListResponse(
        recommendations=[
            RecommendationItemResponse(
                resource=ResourceResponse.model_validate(item.resource),
                score=item.score.score,
                contributions=item.score.contributions.as_dict(),
                covered_gap_slugs=list(item.score.covered_slugs),
                personalised=item.score.personalised,
                explanation=item.explanation,
            )
            for item in result.recommendations
        ],
        gaps=[
            RecommendationGapResponse.model_validate(gap, from_attributes=True)
            for gap in result.gaps
        ],
        degraded=list(result.degraded),
        personalised=result.personalised,
    )


@router.get(
    "",
    response_model=RecommendationListResponse,
    summary="Recommend catalogue resources for the caller",
    description=(
        "Resolves the caller's knowledge gaps from an explicit roadmap or from "
        "measured mastery, ranks catalogue resources by mastery-weighted coverage, "
        "difficulty fit, trust and recency, and explains each pick from its score "
        "decomposition. A gap with no catalogue coverage is reported in "
        "`degraded`, never filled with an unrelated resource."
    ),
)
async def list_recommendations(
    context: ContextDep,
    session: TenantSessionDep,
    settings: SettingsDep,
    course_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    difficulty_max: Annotated[int | None, Query(ge=1, le=5)] = None,
) -> RecommendationListResponse:
    result = await recommend_for_user(
        session,
        context,
        settings,
        user_id=context.user_id,
        course_id=course_id,
        limit=limit,
        difficulty_max=difficulty_max,
    )
    return _list_response(result)


@router.get(
    "/resources",
    response_model=CataloguePageResponse,
    summary="Search the curated catalogue",
    description=(
        "Filter the global, curated resource catalogue by type, trust level and a "
        "free-text query over title, description and provider. Paginated, ordered "
        "by title."
    ),
)
async def search_resources(
    session: TenantSessionDep,
    resource_type: Annotated[ResourceType | None, Query()] = None,
    trust: Annotated[SourceTrust | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CataloguePageResponse:
    catalogue = ResourceCatalogue(session)
    page = await catalogue.search(
        resource_type=resource_type,
        trust=trust,
        query=q,
        limit=limit,
        offset=offset,
    )
    return CataloguePageResponse(
        items=[ResourceResponse.model_validate(row) for row in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{resource_id}",
    response_model=ResourceWithConceptsResponse,
    summary="One catalogue resource and its concept coverage",
    description="Returns the resource's provenance, metadata and the concept slugs it covers.",
)
async def get_resource(
    resource_id: uuid.UUID, session: TenantSessionDep
) -> ResourceWithConceptsResponse:
    catalogue = ResourceCatalogue(session)
    found = await catalogue.get_with_concepts(resource_id)
    if found is None:
        msg = "Resource not found."
        raise NotFoundError(msg)
    resource, concept_slugs = found
    base = ResourceResponse.model_validate(resource)
    return ResourceWithConceptsResponse(
        **base.model_dump(),
        concept_slugs=list(concept_slugs),
    )


@router.post(
    "/seed",
    response_model=SeedReportResponse,
    dependencies=[Depends(require_role(UserRole.OWNER, UserRole.ADMIN))],
    summary="Seed the curated catalogue (admin only)",
    description=(
        "Inserts any curated resource that is not already present, keyed on URL, "
        "and never overwrites an existing row's metadata. Idempotent, so it is "
        "safe to re-run. Admin-only because it writes global data shared by every "
        "tenant."
    ),
)
async def seed_resources(session: TenantSessionDep) -> SeedReportResponse:
    report = await seed_catalogue(session)
    return SeedReportResponse(
        total=report.total,
        inserted=report.inserted,
        skipped=report.skipped,
        concept_links_inserted=report.concept_links_inserted,
    )


__all__ = ["router"]
