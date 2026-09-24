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

from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.db.tenancy import TenantScope
from coursellm.rag.ingestion.embedders import Embedder
from coursellm.rag.retrieval.lexical import lexical_search
from coursellm.rag.retrieval.semantic import semantic_search
from coursellm.rag.retrieval.types import RetrievalFilters, RetrievalOutcome


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
    semantic = await run_semantic_only(session, scope, settings, query=query, filters=filters, k=k)
    lexical = await run_lexical_only(session, scope, settings, query=query, filters=filters, k=k)
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
    return await semantic_search(
        session, scope, settings, query=query, filters=filters, k=k, embedder=embedder
    )


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
    return await lexical_search(session, scope, settings, query=query, filters=filters, k=k)
