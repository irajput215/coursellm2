"""Hybrid retrieval: tenant-scoped semantic and BM25 lexical retrieval.

The public surface is deliberately small and side-effect free. Every entry point
takes an existing :class:`~sqlalchemy.ext.asyncio.AsyncSession` and a
:class:`~coursellm.db.tenancy.TenantScope`; the scope — not a filter object — is
the tenant authority, and the session is expected to have the tenancy GUC set by
the caller (normally through :func:`coursellm.db.tenancy.tenant_session`).

* :func:`semantic_search` — pgvector HNSW cosine similarity, tenant predicate
  inside the scan.
* :func:`lexical_search` — Okapi BM25 over the materialised term statistics.
* :func:`hybrid_search` — both ranked lists; fusion (RRF) arrives in PR 6.
"""

from __future__ import annotations

from coursellm.rag.retrieval.filters import build_predicates
from coursellm.rag.retrieval.hybrid import (
    hybrid_search,
    run_lexical_only,
    run_semantic_only,
)
from coursellm.rag.retrieval.lexical import lexical_search
from coursellm.rag.retrieval.semantic import semantic_search
from coursellm.rag.retrieval.types import RetrievalFilters, RetrievalOutcome, SearchResult

__all__ = [
    "RetrievalFilters",
    "RetrievalOutcome",
    "SearchResult",
    "build_predicates",
    "hybrid_search",
    "lexical_search",
    "run_lexical_only",
    "run_semantic_only",
    "semantic_search",
]
