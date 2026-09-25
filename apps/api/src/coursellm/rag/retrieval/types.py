"""The data contract of the retrieval layer.

Three types, and one invariant that the previous implementation got wrong and
that this module exists to make impossible to get wrong again:

**``score`` means exactly one thing per retriever, and it is never a distance.**

* Semantic: cosine **similarity** in ``[-1, 1]``, higher is better. pgvector's
  ``<=>`` operator returns a cosine *distance*; it is converted with
  ``score = 1 - distance`` at the boundary, in :mod:`coursellm.rag.retrieval.semantic`,
  and the raw distance is never stored on a result.
* Lexical: the raw BM25 score, higher is better. It is never normalised or
  inverted here; fusion (PR 6) uses *ranks*, so the magnitude of a score must
  not be distorted before it can be inspected.

The prototype overloaded one ``score`` field with three meanings — a distance in
the semantic retriever, a rank-like value in the lexical one, and a min-max
normalised similarity after fusion — and that ambiguity was the direct cause of
the fusion bug. A consumer that cannot tell whether a score is "bigger is
better" cannot fuse two lists correctly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from coursellm.db.models.content import SourceType


class RetrievalFilters(BaseModel):
    """User-visible narrowing of the eligible candidate set.

    ``tenant_id`` is deliberately **absent**. Tenancy is not a filter a caller
    may choose: it comes from the authenticated :class:`~coursellm.db.tenancy.TenantScope`
    and is applied by the retrievers (and by Row-Level Security) whether or not
    a filter object is supplied. If ``tenant_id`` were a field, a request model,
    a tool argument or an LLM-generated filter object could widen the query to
    another tenant; because it is not, there is nothing to tamper with.

    ``extra="forbid"`` is the other half of that guarantee: an attempt to pass
    ``tenant_id=...`` is a validation error rather than a silently ignored key.
    """

    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    topic: str | None = None
    page: int | None = None
    source_types: list[SourceType] | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One ranked passage from one retriever.

    ``rank`` is 1-based and dense: ranks are ``1..len(results)``. ``score`` is
    cosine similarity for the semantic retriever and BM25 for the lexical one;
    see the module docstring. ``retriever`` names which one produced this row so
    that a fused list (PR 6) can still explain each candidate.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID
    content: str
    page: int | None
    topic: str | None
    token_count: int
    source_type: SourceType
    rank: int
    score: float
    retriever: Literal["semantic", "lexical"]
    #: Position of the chunk inside its document, and whether it begins
    #: mid-sentence. Carried on the result so the context assembler can merge
    #: adjacent chunks into continuous prose without a second query.
    chunk_index: int | None = None
    starts_mid_sentence: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    """The result of one retriever, including how it degraded.

    ``degraded`` is a list of machine-readable reasons the retriever returned
    less than it could have (``semantic_unavailable``, ``lexical_unavailable``,
    ``empty_query_terms``, ``no_corpus_stats``). It is deliberately separate
    from ``results``: an empty list with no degradation is a legitimate "nothing
    matched", while an empty list *with* a reason is a partial outage the caller
    must surface. An empty list is never an exception here — retrieval degrades,
    it does not abort the request.
    """

    results: list[SearchResult] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    retriever: str = "unknown"
