"""Run both retrievers and return their two ranked lists.

**Fusion is PR 6.** This module deliberately returns the two
:class:`RetrievalOutcome` objects — each with 1-based ranks and a per-retriever
score — rather than a single fused score. The prototype fused here, by min-max
normalising one list and inverting the other, and the result was a ranking that
changed whenever the candidate pool changed. Keeping the lists separate makes
the next change (real Reciprocal Rank Fusion) a pure function over ranks and
testable without a database.

The two retrievers run **sequentially on the shared session**, not under
``asyncio.gather``. A single :class:`~sqlalchemy.ext.asyncio.AsyncSession` is not
safe for concurrent use: two coroutines issuing statements on one connection
interleave transaction state — including the transaction-local
``app.tenant_id`` and ``hnsw.ef_search`` settings — and SQLAlchemy raises
``InvalidRequestError`` when it notices. Concurrency would require two sessions
and two pooled connections per query; the latency saved does not justify tying
up a second connection on the hot path, especially since both retrievers are
index-backed and sub-millisecond at this scale.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.db.tenancy import TenantScope
from coursellm.observability import metrics, tracing
from coursellm.observability.attributes import (
    COURSELLM_CONFIG_VERSION,
    RETRIEVAL_CANDIDATES_IN,
    RETRIEVAL_CANDIDATES_OUT,
    RETRIEVAL_DEGRADED,
    RETRIEVAL_DURATION_MS,
    RETRIEVAL_FILTERS,
    RETRIEVAL_STAGE,
    RETRIEVAL_TOP_K_PER_RETRIEVER,
    SPAN_RETRIEVAL,
    SPAN_RETRIEVAL_LEXICAL,
    SPAN_RETRIEVAL_SEMANTIC,
)
from coursellm.rag.ingestion.embedders import Embedder
from coursellm.rag.retrieval.lexical import lexical_search
from coursellm.rag.retrieval.semantic import semantic_search
from coursellm.rag.retrieval.types import RetrievalFilters, RetrievalOutcome


def filter_names(filters: RetrievalFilters) -> list[str]:
    """The *names* of the filters in force, never their values.

    A span records which narrowing was applied (``course_id``, ``document_id``)
    so a recall failure is attributable; the value is an identifier and belongs
    in neither a span attribute nor a log line.
    """
    return [name for name, value in filters.model_dump().items() if value is not None]


async def hybrid_search(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    query: str,
    filters: RetrievalFilters,
    k_per_retriever: int | None = None,
) -> tuple[RetrievalOutcome, RetrievalOutcome]:
    """Return ``(semantic, lexical)``, each ranked independently.

    ``k_per_retriever`` defaults to ``settings.retrieval_top_k_per_retriever``.
    A retriever that degraded returns an empty list with a reason rather than
    raising, so the caller always receives two outcomes.
    """
    k = k_per_retriever if k_per_retriever is not None else settings.retrieval_top_k_per_retriever
    with tracing.span(
        SPAN_RETRIEVAL,
        **{
            RETRIEVAL_TOP_K_PER_RETRIEVER: k,
            RETRIEVAL_FILTERS: filter_names(filters),
            COURSELLM_CONFIG_VERSION: settings.retrieval_config_version,
        },
    ) as record:
        semantic = await run_semantic_only(
            session, scope, settings, query=query, filters=filters, k=k
        )
        lexical = await run_lexical_only(
            session, scope, settings, query=query, filters=filters, k=k
        )
        record.set_attributes(
            {
                RETRIEVAL_CANDIDATES_IN: len(semantic.results) + len(lexical.results),
                RETRIEVAL_CANDIDATES_OUT: len(semantic.results) + len(lexical.results),
                RETRIEVAL_DEGRADED: bool(semantic.degraded or lexical.degraded),
            }
        )
    return semantic, lexical


async def run_semantic_only(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    query: str,
    filters: RetrievalFilters,
    k: int,
    embedder: Embedder | None = None,
) -> RetrievalOutcome:
    """The semantic half alone, so its degradation path is directly testable."""
    started = time.perf_counter()
    with tracing.span(
        SPAN_RETRIEVAL_SEMANTIC,
        **{RETRIEVAL_STAGE: "semantic", RETRIEVAL_TOP_K_PER_RETRIEVER: k},
    ) as record:
        outcome = await semantic_search(
            session, scope, settings, query=query, filters=filters, k=k, embedder=embedder
        )
        _record_stage(record, outcome, started=started, stage="semantic", retriever="semantic")
        return outcome


async def run_lexical_only(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    *,
    query: str,
    filters: RetrievalFilters,
    k: int,
) -> RetrievalOutcome:
    """The lexical half alone, so its degradation path is directly testable."""
    started = time.perf_counter()
    with tracing.span(
        SPAN_RETRIEVAL_LEXICAL,
        **{RETRIEVAL_STAGE: "lexical", RETRIEVAL_TOP_K_PER_RETRIEVER: k},
    ) as record:
        outcome = await lexical_search(session, scope, settings, query=query, filters=filters, k=k)
        _record_stage(record, outcome, started=started, stage="lexical", retriever="lexical")
        return outcome


def _record_stage(
    record: tracing.SpanRecorder,
    outcome: RetrievalOutcome,
    *,
    started: float,
    stage: str,
    retriever: str,
) -> None:
    duration_ms = (time.perf_counter() - started) * 1000.0
    degraded = bool(outcome.degraded)
    record.set_attributes(
        {
            RETRIEVAL_CANDIDATES_OUT: len(outcome.results),
            RETRIEVAL_DURATION_MS: duration_ms,
            RETRIEVAL_DEGRADED: degraded,
        }
    )
    metrics.record_retrieval_stage(
        stage=stage,
        duration_ms=duration_ms,
        candidates=len(outcome.results),
        retriever=retriever,
        degraded=degraded,
    )
    metrics.record_degraded(*outcome.degraded)
