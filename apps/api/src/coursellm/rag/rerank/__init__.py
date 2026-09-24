"""Reranking: reorder the fused candidate list and select the final passages.

The public surface is:

* :func:`rank` — fuse, rerank, floor and truncate into a
  :class:`RankingOutcome`.
* :class:`CrossEncoderReranker` / :class:`LexicalReranker` — the two
  :class:`Reranker` implementations.
* :func:`get_reranker` — the factory that degrades to the lexical substitute.
"""

from __future__ import annotations

from coursellm.rag.rerank.pipeline import (
    RERANK_FLOOR_REMOVED_ALL,
    RERANK_TIMEOUT,
    RERANKER_UNAVAILABLE,
    RankedPassage,
    RankingOutcome,
    rank,
)
from coursellm.rag.rerank.rerankers import (
    LEXICAL_RERANKER_MODEL_ID,
    RERANK_MAX_LENGTH,
    CrossEncoderReranker,
    LexicalReranker,
    RerankedResult,
    Reranker,
    get_reranker,
)

__all__ = [
    "LEXICAL_RERANKER_MODEL_ID",
    "RERANKER_UNAVAILABLE",
    "RERANK_FLOOR_REMOVED_ALL",
    "RERANK_MAX_LENGTH",
    "RERANK_TIMEOUT",
    "CrossEncoderReranker",
    "LexicalReranker",
    "RankedPassage",
    "RankingOutcome",
    "RerankedResult",
    "Reranker",
    "get_reranker",
    "rank",
]
