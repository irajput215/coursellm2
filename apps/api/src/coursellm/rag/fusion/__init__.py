"""Fusion: combine the two retriever rank lists into one candidate ranking.

Reciprocal Rank Fusion is the only fusion strategy in the system (ADR-0005).
The public surface is pure and synchronous so it can be exercised without a
database, a model, or an event loop:

* :func:`rrf` — the scoring function over any number of ranked id lists.
* :func:`fuse` — fuse two :class:`~coursellm.rag.retrieval.types.RetrievalOutcome`
  objects into :class:`FusedResults`.
"""

from __future__ import annotations

from coursellm.rag.fusion.rrf import (
    DEFAULT_RRF_K,
    FusedResult,
    FusedResults,
    fuse,
    rrf,
)

__all__ = [
    "DEFAULT_RRF_K",
    "FusedResult",
    "FusedResults",
    "fuse",
    "rrf",
]
