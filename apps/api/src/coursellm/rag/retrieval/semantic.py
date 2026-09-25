"""Tenant-scoped semantic retrieval over pgvector's HNSW index.

The statement is a single query and the tenant predicate is **inside** it. The
alternative — retrieve the global top-k and discard other tenants' rows in
Python — is not a style choice: pgvector's HNSW scan is approximate, so a small
tenant's rows are frequently not in the global neighbourhood at all. Filtering
after the scan returns zero rows for that tenant while latency looks healthy.
The predicate has to be in the SQL for the planner to choose between the ANN
index and the exact ``(tenant_id, embedding_model)`` index (see
``docs/architecture/rag.md`` §3).

This module degrades rather than raises. A semantic outage must not fail the
request when lexical retrieval can still answer it, so every failure path
returns an empty :class:`RetrievalOutcome` carrying a machine-readable reason in
``degraded``. The hybrid retriever (and, later, RRF) can then fuse whatever the
surviving retriever produced.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.db.models.content import Chunk, ChunkEmbedding, Document
from coursellm.db.tenancy import TenantScope
from coursellm.rag.ingestion.embedders import Embedder, get_embedder
from coursellm.rag.retrieval.filters import build_predicates
from coursellm.rag.retrieval.types import RetrievalFilters, RetrievalOutcome, SearchResult

logger = get_logger(__name__)

# ``ChunkEmbedding.embedding`` is typed ``Mapped[Any]``; pgvector's
# ``cosine_distance`` comparator is attached at runtime but is invisible to a
# static checker, so the column is aliased to ``Any`` once here.
_EMBEDDING: Any = ChunkEmbedding.embedding


async def semantic_search(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    query: str,
    filters: RetrievalFilters,
    k: int,
    embedder: Embedder | None = None,
) -> RetrievalOutcome:
    """Return up to ``k`` passages nearest ``query`` for ``scope``'s tenant.

    ``score`` is cosine similarity: the raw pgvector ``<=>`` distance is
    converted with ``1 - distance`` before construction, so no consumer ever
    sees a distance where a similarity is expected.

    Degradation is deliberate:

    * an embedder that is missing or fails (no model on disk, a provider error,
      a network call that times out) yields ``degraded=["semantic_unavailable"]``;
    * a database error yields the same.

    The result is an empty outcome, never an exception, because the caller can
    still serve the query from lexical retrieval. Raising here would turn a
    half-degraded retrieval stack into a failed request.
    """
    started = time.perf_counter()
    resolved: Embedder | None = embedder

    if resolved is None:
        try:
            resolved = get_embedder(settings)
        except Exception as exc:  #
            logger.warning(
                "semantic_retrieval_degraded",
                reason="embedder_unavailable",
                exc_type=type(exc).__name__,
                tenant_id=str(scope.tenant_id),
            )
            return _empty("semantic_unavailable", started)

    try:
        query_vector = await resolved.embed_query(query)
    except Exception as exc:  #
        logger.warning(
            "semantic_retrieval_degraded",
            reason="query_embedding_failed",
            exc_type=type(exc).__name__,
            tenant_id=str(scope.tenant_id),
        )
        return _empty("semantic_unavailable", started)

    try:
        # ``hnsw.ef_search`` is a session/transaction setting: recall is traded
        # for latency without rebuilding the index, and ``is_local => true``
        # scopes it to this transaction so a pooled connection never carries it.
        await session.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"),
            {"value": str(settings.hnsw_ef_search)},
        )

        predicates, params = build_predicates(
            filters,
            chunk_alias=Chunk,
            settings=settings,
            tenant_id=scope.tenant_id,
        )
        distance = _EMBEDDING.cosine_distance(query_vector)
        statement = (
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.course_id,
                Chunk.content,
                Chunk.page,
                Chunk.topic,
                Chunk.token_count,
                Chunk.chunk_index,
                Chunk.starts_mid_sentence,
                Document.source_type,
                distance.label("distance"),
            )
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                ChunkEmbedding.tenant_id == scope.tenant_id,
                *predicates,
                ChunkEmbedding.embedding_model == resolved.model_id,
                ChunkEmbedding.dim == resolved.dim,
            )
            # Ordered by distance, then by chunk id. The secondary key is not
            # cosmetic: pgvector distances tie often on small corpora and under
            # the hashing embedder, and without a deterministic tie-break the
            # database may return tied rows in any order. That makes a rank-based
            # fusion stage (RRF) non-reproducible between two runs of the same
            # query, which in turn makes an evaluation regression unattributable.
            # The lexical retriever already tie-breaks this way.
            .order_by(distance, Chunk.id)
            .limit(k)
        )
        rows = (await session.execute(statement, params)).mappings().all()
    except SQLAlchemyError as exc:
        logger.exception(
            "semantic_retrieval_degraded",
            reason="database_error",
            exc_type=type(exc).__name__,
            tenant_id=str(scope.tenant_id),
        )
        return _empty("semantic_unavailable", started)

    results = [
        SearchResult(
            chunk_id=row["id"],
            document_id=row["document_id"],
            course_id=row["course_id"],
            content=row["content"],
            page=row["page"],
            topic=row["topic"],
            token_count=row["token_count"],
            source_type=row["source_type"],
            rank=rank,
            score=1.0 - float(row["distance"]),
            retriever="semantic",
            chunk_index=row["chunk_index"],
            starts_mid_sentence=bool(row["starts_mid_sentence"]),
        )
        for rank, row in enumerate(rows, start=1)
    ]
    return RetrievalOutcome(
        results=results,
        degraded=[],
        latency_ms=(time.perf_counter() - started) * 1000.0,
        retriever="semantic",
    )


def _empty(reason: str, started: float) -> RetrievalOutcome:
    return RetrievalOutcome(
        results=[],
        degraded=[reason],
        latency_ms=(time.perf_counter() - started) * 1000.0,
        retriever="semantic",
    )
