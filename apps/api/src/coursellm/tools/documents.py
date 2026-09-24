"""Document and course reading tools: ``search_documents``, ``search_books``,
``search_course``.

``search_documents`` and ``search_books`` are the same pipeline as the
``retrieval`` and ``rerank`` graph nodes — ``rerank(rrf(hybrid_search(...)))`` —
with a source-type filter for the books variant. The graph decomposes that
composition so each half has its own span and budget; the tool keeps it whole so
an agent can ask for evidence in one call. One implementation, two observation
points.

``search_course`` reads the course row and its ready documents under the
tenant's own repository scope. Course *modules*, *lectures* and *topics* have no
table in this schema revision, so the outline carries the documents that do exist
and an empty ``modules`` list rather than invented structure.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coursellm.db.models.content import SourceType
from coursellm.db.tenancy import TenantScope
from coursellm.rag.rerank.pipeline import RankedPassage, rank
from coursellm.rag.retrieval.hybrid import hybrid_search
from coursellm.rag.retrieval.types import RetrievalFilters
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.tools.registry import (
    Permission,
    RetryableToolError,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

_MAX_QUERY = 2000


class SearchDocumentsArgs(BaseModel):
    """Arguments for the grounded-evidence tools.

    There is no ``tenant_id`` and no ``user_id`` field. Scope is injected from
    state; supplying one is an unknown-field validation error, which is the
    structural half of "tenancy is a property of the query, not a filter".
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=_MAX_QUERY)
    course_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    topic: str | None = Field(default=None, max_length=300)
    page: int | None = Field(default=None, ge=1)
    source_types: list[SourceType] | None = None
    k: int = Field(default=20, ge=1, le=100)


class SearchBooksArgs(BaseModel):
    """Arguments for textbook-only evidence."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=_MAX_QUERY)
    course_id: uuid.UUID | None = None
    k: int = Field(default=20, ge=1, le=100)


class SearchCourseArgs(BaseModel):
    """Arguments for the course-structure tool."""

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID
    query: str | None = Field(default=None, max_length=_MAX_QUERY)
    module: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


class CourseDocument(BaseModel):
    """One document in a course outline."""

    document_id: str
    filename: str
    source_type: str
    status: str
    page_count: int | None = None


class CourseOutline(BaseModel):
    """Course structure as it exists in this schema revision."""

    course_id: str
    name: str | None = None
    code: str | None = None
    modules: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[CourseDocument] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


def ranked_to_document(passage: RankedPassage, *, graph_rank: int | None = None) -> dict[str, Any]:
    """Convert a ranked passage into the state's ``RetrievedDocument`` shape.

    Returned as a plain mapping because ``RetrievedDocument`` is a ``TypedDict``:
    it is a state channel, not a wire model, and validating it would add cost
    without protecting a boundary.
    """
    return {
        "chunk_id": str(passage.chunk_id),
        "document_id": str(passage.source.document_id),
        "content": passage.source.content,
        "page": passage.source.page,
        "topic": passage.source.topic,
        "source_type": passage.source.source_type.value,
        "semantic_rank": passage.semantic_rank,
        "lexical_rank": passage.lexical_rank,
        "graph_rank": graph_rank,
        "rrf_score": passage.rrf_score,
        "rerank_score": passage.rerank_score,
        "citation_id": f"S{passage.final_rank}",
    }


async def search_documents(args: SearchDocumentsArgs, ctx: ToolContext) -> list[dict[str, Any]]:
    """Hybrid retrieval, fusion and reranking scoped to the caller's tenant."""
    return await _search(
        args,
        ctx,
        source_types=args.source_types,
    )


async def search_books(args: SearchBooksArgs, ctx: ToolContext) -> list[dict[str, Any]]:
    """The same pipeline with ``source_type = "book"`` forced."""
    documents = SearchDocumentsArgs(
        query=args.query,
        course_id=args.course_id,
        source_types=[SourceType.BOOK],
        k=args.k,
    )
    return await _search(documents, ctx, source_types=[SourceType.BOOK])


async def search_course(args: SearchCourseArgs, ctx: ToolContext) -> CourseOutline:
    """Read the course and its ready documents under the tenant's own scope."""
    if ctx.session is None:
        raise RetryableToolError("search_course requires a tenant-scoped session.")
    scope = TenantScope(ctx.tenant_id)
    course = await CourseRepository(ctx.session, scope).get(args.course_id)
    if course is None:
        return CourseOutline(
            course_id=str(args.course_id),
            degraded=["course_not_found"],
        )
    documents = await DocumentRepository(ctx.session, scope).list_for_course(
        course.id, limit=args.limit
    )
    return CourseOutline(
        course_id=str(course.id),
        name=course.name,
        code=course.code,
        documents=[
            CourseDocument(
                document_id=str(document.id),
                filename=document.filename,
                source_type=document.source_type.value,
                status=document.status.value,
                page_count=document.page_count,
            )
            for document in documents
        ],
    )


async def _search(
    args: SearchDocumentsArgs,
    ctx: ToolContext,
    *,
    source_types: list[SourceType] | None,
) -> list[dict[str, Any]]:
    if ctx.session is None:
        raise RetryableToolError("Document search requires a tenant-scoped session.")
    filters = RetrievalFilters(
        course_id=args.course_id,
        document_id=args.document_id,
        topic=args.topic,
        page=args.page,
        source_types=source_types,
    )
    semantic, lexical = await hybrid_search(
        ctx.session,
        TenantScope(ctx.tenant_id),
        ctx.settings,
        query=args.query,
        filters=filters,
        k_per_retriever=args.k,
    )
    ranking = await rank(
        query=args.query,
        semantic=semantic,
        lexical=lexical,
        settings=ctx.settings,
    )
    return [ranked_to_document(passage) for passage in ranking.results]


def register(registry: ToolRegistry) -> None:
    """Register the three document/course tools into ``registry``."""
    registry.register(
        ToolSpec(
            name="search_documents",
            description=(
                "Grounded evidence from the tenant's own documents: hybrid "
                "retrieval fused with RRF and reranked by a cross-encoder."
            ),
            parameters=SearchDocumentsArgs,
            required_permissions=frozenset({Permission.DOCUMENTS_READ}),
            side_effects="none",
            timeout_ms=7000,
            handler=search_documents,
        )
    )
    registry.register(
        ToolSpec(
            name="search_books",
            description=(
                "Evidence restricted to source_type='book' (textbook chapters), "
                "through the same retrieval and reranking pipeline."
            ),
            parameters=SearchBooksArgs,
            required_permissions=frozenset({Permission.DOCUMENTS_READ}),
            side_effects="none",
            timeout_ms=7000,
            handler=search_books,
        )
    )
    registry.register(
        ToolSpec(
            name="search_course",
            description=(
                "Course structure: the course itself and the documents within it. "
                "Module and topic nodes are added in a later revision."
            ),
            parameters=SearchCourseArgs,
            required_permissions=frozenset({Permission.COURSES_READ}),
            side_effects="none",
            timeout_ms=3000,
            handler=search_course,
        )
    )


__all__ = [
    "CourseDocument",
    "CourseOutline",
    "SearchBooksArgs",
    "SearchCourseArgs",
    "SearchDocumentsArgs",
    "ranked_to_document",
    "register",
    "search_books",
    "search_course",
    "search_documents",
]
