"""Tenant-scoped lexical retrieval with real Okapi BM25.

PostgreSQL's ``ts_rank`` is not BM25. It has no term-frequency saturation, no
document-length normalisation and no inverse-document-frequency component, so a
long document that repeats a rare term is not scored the way BM25 scores it.
This module computes BM25 explicitly, in one scoring statement, from the
materialised statistics written by ingestion:

* ``chunk_terms`` — per-chunk term frequency ``tf(t, d)``;
* ``tenant_lexical_stats`` — per-tenant document frequency ``df(t)``;
* ``tenant_corpus_stats`` — tenant ``N`` (chunk count) and mean chunk length.

    score(d) = SUM over query terms t of
                 IDF(t) * ( tf(t, d) * (k1 + 1) )
                 / ( tf(t, d) + k1 * (1 - b + b * len(d) / avgdl) )
    IDF(t) = ln( 1 + (N - df(t) + 0.5) / (df(t) + 0.5) )

``score`` is the raw BM25 value, higher is better. It is never inverted or
normalised here: fusion (PR 6) consumes *ranks*, so distorting the magnitude
before it can be inspected would discard information for nothing.

Ordering is ``score DESC, chunk_id`` so equal scores have a stable, reproducible
order. RRF depends on stable ranks, and a hash-join whose output order changes
between runs is a flaky test waiting to happen.

As in :mod:`coursellm.rag.retrieval.semantic`, failures degrade rather than
raise: an empty outcome with a reason in ``degraded`` lets the hybrid retriever
fall back to the other retriever instead of failing the request.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.db.models.content import (
    Chunk,
    ChunkTerm,
    Document,
    TenantCorpusStats,
    TenantLexicalStats,
)
from coursellm.db.tenancy import TenantScope
from coursellm.rag.analyzers import STOPWORDS, normalize_query
from coursellm.rag.retrieval.filters import build_predicates
from coursellm.rag.retrieval.types import RetrievalFilters, RetrievalOutcome, SearchResult

logger = get_logger(__name__)

_GROUPED_COLUMNS = (
    Chunk.id,
    Chunk.document_id,
    Chunk.course_id,
    Chunk.content,
    Chunk.page,
    Chunk.topic,
    Chunk.token_count,
    Document.source_type,
)


async def lexical_search(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    query: str,
    filters: RetrievalFilters,
    k: int,
) -> RetrievalOutcome:
    """Return up to ``k`` passages ranked by BM25 for ``scope``'s tenant.

    An empty term list is not an error and not a full-table scan: it is an empty
    outcome with ``degraded=["empty_query_terms"]``. A tenant with no corpus
    statistics — no chunks — yields ``degraded=["no_corpus_stats"]`` rather than
    a division by zero. A database error yields
    ``degraded=["lexical_unavailable"]``. None of these raise.
    """
    started = time.perf_counter()
    terms = _query_terms(query)
    if not terms:
        # A stopword-only query cannot match anything, because the index path
        # removes stopwords before writing ``chunk_terms``. ``normalize_query``
        # deliberately falls back to the unfiltered tokens so that *semantic*
        # retrieval still has something to embed; for BM25 those tokens are
        # unmatchable, so they are dropped here rather than sent to the database
        # to scan for terms that cannot exist.
        return _empty("empty_query_terms", started)

    try:
        predicates, params = build_predicates(
            filters,
            chunk_alias=Chunk,
            settings=settings,
            tenant_id=scope.tenant_id,
        )

        k1 = settings.bm25_k1
        b = settings.bm25_b
        idf = func.ln(
            1.0
            + (TenantCorpusStats.doc_count - TenantLexicalStats.doc_freq + 0.5)
            / (TenantLexicalStats.doc_freq + 0.5)
        )
        # ``NULLIF`` guards the empty-tenant case: avg_doc_len is 0 when the
        # tenant has no chunks, and an explicit check would otherwise have to be
        # repeated at every call site.
        length_ratio = Chunk.token_count / func.nullif(TenantCorpusStats.avg_doc_len, 0.0)
        saturation = ChunkTerm.tf + k1 * (1.0 - b + b * length_ratio)
        score = func.sum(idf * (ChunkTerm.tf * (k1 + 1.0)) / saturation)

        statement = (
            select(*_GROUPED_COLUMNS, score.label("score"))
            .select_from(ChunkTerm)
            .join(
                TenantLexicalStats,
                (TenantLexicalStats.tenant_id == ChunkTerm.tenant_id)
                & (TenantLexicalStats.term == ChunkTerm.term),
            )
            .join(Chunk, Chunk.id == ChunkTerm.chunk_id)
            .join(TenantCorpusStats, TenantCorpusStats.tenant_id == ChunkTerm.tenant_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                ChunkTerm.tenant_id == scope.tenant_id,
                *predicates,
                ChunkTerm.term.in_(terms),
            )
            .group_by(*_GROUPED_COLUMNS)
            .order_by(score.desc(), Chunk.id)
            .limit(k)
        )
        rows = (await session.execute(statement, params)).mappings().all()

        if not rows and not await _tenant_has_corpus(session, scope.tenant_id):
            # No statistics row means the tenant has no chunks; distinguishing
            # that from "the terms matched nothing" costs one cheap probe only
            # on the empty path, so the normal path stays a single statement.
            return _empty("no_corpus_stats", started)
    except SQLAlchemyError as exc:
        logger.exception(
            "lexical_retrieval_degraded",
            reason="database_error",
            exc_type=type(exc).__name__,
            tenant_id=str(scope.tenant_id),
        )
        return _empty("lexical_unavailable", started)

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
            score=round(float(row["score"]), 6),
            retriever="lexical",
        )
        for rank, row in enumerate(rows, start=1)
    ]
    return RetrievalOutcome(
        results=results,
        degraded=[],
        latency_ms=(time.perf_counter() - started) * 1000.0,
        retriever="lexical",
    )


def _query_terms(query: str) -> list[str]:
    """Analysed query terms that can actually exist in ``chunk_terms``."""
    return [term for term in normalize_query(query) if term not in STOPWORDS]


async def _tenant_has_corpus(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    statement = select(TenantCorpusStats.tenant_id).where(TenantCorpusStats.tenant_id == tenant_id)
    return (await session.execute(statement)).first() is not None


def _empty(reason: str, started: float) -> RetrievalOutcome:
    return RetrievalOutcome(
        results=[],
        degraded=[reason],
        latency_ms=(time.perf_counter() - started) * 1000.0,
        retriever="lexical",
    )
