"""Translate a :class:`RetrievalFilters` into SQL predicates.

Two rules shape this module:

1. **The tenant predicate is always first and is never optional.** It is not
   derived from the filter object — the filter object cannot carry a tenant at
   all — it is supplied from the ambient :class:`~coursellm.db.tenancy.TenantScope`.
   A filter set that narrows nothing still produces a tenant-scoped query, which
   is the property the security suite pins.
2. **``source_types`` must not duplicate rows.** A plain ``JOIN documents`` would
   multiply rows if the join ever became one-to-many, and — more importantly — it
   forces the ANN query to reason about a join. An ``EXISTS`` subquery keeps the
   outer query a single row per chunk, so it composes with the pgvector scan
   without changing its cardinality.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, bindparam, select
from sqlalchemy.orm import aliased

from coursellm.core.config import Settings
from coursellm.db.models.content import Document, SourceType
from coursellm.rag.retrieval.types import RetrievalFilters

# Bind-parameter names. Namespaced with ``filter_`` so a filter parameter can
# never collide with the retriever's own ``tenant_id``/``terms``/``k`` binds.
_COURSE = "filter_course_id"
_DOCUMENT = "filter_document_id"
_TOPIC = "filter_topic"
_PAGE = "filter_page"


def build_predicates(
    filters: RetrievalFilters,
    *,
    chunk_alias: Any,
    settings: Settings,
    tenant_id: uuid.UUID | None = None,
) -> tuple[list[ColumnElement[bool]], dict[str, Any]]:
    """Build the predicate list and bind parameters for one retriever query.

    ``chunk_alias`` is the ``chunks`` column source the retriever has placed in
    its ``FROM`` — normally the :class:`~coursellm.db.models.content.Chunk` class,
    but typing it as ``Any`` lets a caller pass an ``aliased(Chunk)`` without the
    function knowing the alias's generated name.

    ``tenant_id`` is bound into the leading predicate. It is a keyword argument
    rather than a field on ``filters`` so that the tenancy boundary is passed in
    from the session/scope and cannot travel through a caller-controlled object.

    When ``settings.metadata_filtering_enabled`` is false only the tenant
    predicate survives: metadata filters are a feature that can be switched off,
    tenancy is not.
    """
    predicates: list[ColumnElement[bool]] = [chunk_alias.tenant_id == bindparam("tenant_id")]
    params: dict[str, Any] = {}
    if tenant_id is not None:
        params["tenant_id"] = tenant_id

    if not settings.metadata_filtering_enabled:
        return predicates, params

    if filters.course_id is not None:
        predicates.append(chunk_alias.course_id == bindparam(_COURSE))
        params[_COURSE] = filters.course_id
    if filters.document_id is not None:
        predicates.append(chunk_alias.document_id == bindparam(_DOCUMENT))
        params[_DOCUMENT] = filters.document_id
    if filters.topic is not None:
        predicates.append(chunk_alias.topic == bindparam(_TOPIC))
        params[_TOPIC] = filters.topic
    if filters.page is not None:
        predicates.append(chunk_alias.page == bindparam(_PAGE))
        params[_PAGE] = filters.page
    if filters.source_types:
        predicates.append(_source_type_exists(filters.source_types, chunk_alias=chunk_alias))

    return predicates, params


def _source_type_exists(source_types: list[SourceType], *, chunk_alias: Any) -> ColumnElement[bool]:
    """``EXISTS`` over ``documents`` rather than a join.

    ``source_type`` lives on ``documents``, so it is the one filter that could
    have been expressed as a join. Using ``EXISTS`` keeps the ANN statement's
    result cardinality exactly one row per embedding/chunk, which is what the
    ``ORDER BY embedding <=> :vector LIMIT k`` shape assumes.

    ``documents`` is aliased because both retrievers already place the real
    ``documents`` table in their ``FROM`` (they need ``source_type`` on the
    projected row). Referencing the un-aliased entity inside an ``EXISTS`` while
    it is also in the enclosing query makes SQLAlchemy auto-correlate the
    subquery down to nothing and fail with "returned no FROM clauses due to
    auto-correlation". A distinct alias is a separate FROM entry, so the
    subquery stays well-formed and only ``chunks`` is correlated.
    """
    document = aliased(Document)
    return (
        select(document.id)
        .where(
            document.id == chunk_alias.document_id,
            document.tenant_id == bindparam("tenant_id"),
            document.source_type.in_(source_types),
        )
        .exists()
    )
