"""The only reader of the global catalogue tables.

This is a repository in the same spirit as the tenant-scoped ones, but it takes
**no** :class:`~coursellm.db.tenancy.TenantScope`: ``resources`` and
``resource_concepts`` are global, so there is nothing to scope and no query that
could leak across a tenant by forgetting a predicate. The constructor still
requires an explicit :class:`~sqlalchemy.ext.asyncio.AsyncSession`, so a caller
cannot reach the catalogue without one.

Every method returns ORM rows or small frozen dataclasses. Scoring is *not* done
here; :mod:`coursellm.recommend.ranking` does that with no I/O at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.db.models.resource import Resource, ResourceConcept
from coursellm.recommend.schemas import ResourceType, SourceTrust

#: Upper bound on rows read when resolving coverage for a set of gaps. The
#: catalogue is curated and small; the bound exists so a pathological gap list
#: cannot turn into an unbounded read.
DEFAULT_COVERAGE_ROW_LIMIT = 1000

_LIKE_ESCAPE = "\\"


def _like_pattern(query: str) -> str:
    """An ILIKE pattern for a literal user query, escaping wildcards."""
    escaped = (
        query.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    """A catalogue resource together with the gap concepts it covers."""

    resource: Resource
    covered_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CataloguePage:
    """One page of catalogue search results, with the unpaginated total."""

    items: tuple[Resource, ...]
    total: int
    limit: int
    offset: int


class ResourceCatalogue:
    """Read access to ``resources`` and ``resource_concepts``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, resource_id: uuid.UUID) -> Resource | None:
        statement = select(Resource).where(Resource.id == resource_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_with_concepts(
        self, resource_id: uuid.UUID
    ) -> tuple[Resource, tuple[str, ...]] | None:
        """One resource and the sorted concept slugs it covers."""
        resource = await self.get(resource_id)
        if resource is None:
            return None
        slugs = await self.concept_slugs_for(resource_id)
        return resource, slugs

    async def concept_slugs_for(self, resource_id: uuid.UUID) -> tuple[str, ...]:
        statement = (
            select(ResourceConcept.concept_slug)
            .where(ResourceConcept.resource_id == resource_id)
            .order_by(ResourceConcept.concept_slug)
        )
        return tuple((await self._session.execute(statement)).scalars().all())

    async def covering(
        self,
        concept_slugs: Sequence[str],
        *,
        row_limit: int = DEFAULT_COVERAGE_ROW_LIMIT,
    ) -> list[ResourceCandidate]:
        """Every resource that covers at least one of ``concept_slugs``.

        Only catalogue rows are returned: a gap with no matching row simply does
        not appear here, and the caller reports it as uncovered rather than
        filling it with an unrelated resource.
        """
        slugs = [slug for slug in dict.fromkeys(concept_slugs) if slug]
        if not slugs:
            return []
        statement = (
            select(Resource, ResourceConcept.concept_slug)
            .join(ResourceConcept, ResourceConcept.resource_id == Resource.id)
            .where(ResourceConcept.concept_slug.in_(slugs))
            .order_by(Resource.url, ResourceConcept.concept_slug)
            .limit(row_limit)
        )
        rows = (await self._session.execute(statement)).all()

        grouped: dict[uuid.UUID, tuple[Resource, list[str]]] = {}
        for resource, slug in rows:
            entry = grouped.get(resource.id)
            if entry is None:
                grouped[resource.id] = (resource, [str(slug)])
            else:
                entry[1].append(str(slug))
        return [
            ResourceCandidate(resource=resource, covered_slugs=tuple(sorted(covered)))
            for resource, covered in sorted(grouped.values(), key=lambda item: item[0].url)
        ]

    async def search(
        self,
        *,
        resource_type: ResourceType | None = None,
        trust: SourceTrust | None = None,
        query: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> CataloguePage:
        """Filter and paginate the catalogue. Order is title, then URL."""
        base: Select[tuple[Resource]] = select(Resource)
        count_stmt = select(func.count()).select_from(Resource)
        if resource_type is not None:
            base = base.where(Resource.resource_type == resource_type)
            count_stmt = count_stmt.where(Resource.resource_type == resource_type)
        if trust is not None:
            base = base.where(Resource.trust == trust)
            count_stmt = count_stmt.where(Resource.trust == trust)
        if query is not None and query.strip():
            pattern = _like_pattern(query.strip())
            text_filter = or_(
                Resource.title.ilike(pattern, escape=_LIKE_ESCAPE),
                Resource.description.ilike(pattern, escape=_LIKE_ESCAPE),
                Resource.provider.ilike(pattern, escape=_LIKE_ESCAPE),
            )
            base = base.where(text_filter)
            count_stmt = count_stmt.where(text_filter)

        total = int((await self._session.execute(count_stmt)).scalar_one())
        page_stmt = base.order_by(Resource.title, Resource.url).limit(limit).offset(offset)
        items = tuple((await self._session.execute(page_stmt)).scalars().all())
        return CataloguePage(items=items, total=total, limit=limit, offset=offset)

    async def count(self) -> int:
        statement = select(func.count()).select_from(Resource)
        return int((await self._session.execute(statement)).scalar_one())

    async def all_resources(self, *, limit: int = 200, offset: int = 0) -> list[Resource]:
        """The catalogue ordered deterministically, for the non-personalised case."""
        statement = (
            select(Resource).order_by(Resource.title, Resource.url).limit(limit).offset(offset)
        )
        return list((await self._session.execute(statement)).scalars().all())


__all__ = [
    "DEFAULT_COVERAGE_ROW_LIMIT",
    "CataloguePage",
    "ResourceCandidate",
    "ResourceCatalogue",
]
