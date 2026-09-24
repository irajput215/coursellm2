"""Reciprocal Rank Fusion over the two retriever rank lists.

RRF is the whole of the fusion stage. It consumes *ranks* only:

    score(d) = SUM over rank lists L containing d of 1 / (k + rank_L(d))

with a 1-based rank and a constant ``k`` (default 60, from Cormack, Clarke and
Buettcher, 2009). A document present in both lists therefore accumulates two
terms and is rewarded for retriever agreement; a document in one list keeps its
single term.

This module replaces a weighted sum of min-max normalised scores, which had
three defects:

1. **Min-max normalisation is corpus-relative.** Each score was rescaled by the
   best and worst candidates *in that result set*, so one outlier — or a change
   in ``top_k``, which changes the pool — rescaled every other document's
   contribution. The ranking of a fixed ``(query, document)`` pair was not
   stable across requests.
2. **Cosine distance and BM25 are not on comparable scales.** ``0.7 *
   semantic + 0.3 * keyword`` assumes the two distributions are commensurate;
   they are not, and no fixed weight transfers across queries, corpora,
   embedding models or a BM25 parameter change.
3. **The prototype inverted one list and overwrote a score field.** The sign
   convention lived out of band (``invert=True`` for the semantic list), so a
   change of score convention silently inverted fusion, and a chunk present in
   both lists had one explanation field overwritten by assignment rather than
   accumulating.

The price is explicit: **RRF discards per-retriever confidence.** Rank 1 by a
decisive margin scores exactly the same as a document that barely edged into
rank 1, and a weak list still contributes score through its ranks. Magnitude
information is thrown away before reranking, which is why a cross-encoder
reranker follows this stage (see :mod:`coursellm.rag.rerank`).

The module is pure and synchronous: no I/O, no database, no model. That is what
makes fusion testable with hand-computed arithmetic.

Tie-breaking is deterministic — descending ``rrf_score``, then the best
individual rank, then ``chunk_id`` — because near-collisions in
``1 / (k + rank)`` are common and an unstable order would make every downstream
rank meaningless.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from coursellm.rag.retrieval.types import RetrievalOutcome, SearchResult

# ---------------------------------------------------------------------------
# Fusion configuration
# ---------------------------------------------------------------------------
# ``k`` is a tuning knob, not a magic number: it is configurable through
# ``settings.rrf_k`` so that it participates in the retrieval configuration hash
# (ADR-0010) and a quality change is attributable to a configuration change.
# Larger ``k`` flattens the contribution of top ranks; smaller ``k`` sharpens
# it. The default follows the original TREC work.
DEFAULT_RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedResult:
    """One fused candidate, with the evidence for its position.

    ``semantic_rank`` and ``lexical_rank`` are ``None`` when the retriever did
    not return the chunk, so a caller can always explain which list (or lists)
    proposed a passage. ``source`` is the best-ranked :class:`SearchResult` for
    the chunk — from the list that ranked it higher, semantically preferred on a
    tie — and carries the passage text and metadata.
    """

    chunk_id: uuid.UUID
    rrf_score: float
    final_rank: int
    semantic_rank: int | None
    lexical_rank: int | None
    source: SearchResult


@dataclass(frozen=True, slots=True)
class FusedResults:
    """The fused candidate list plus the union of the retrievers' degradations."""

    results: list[FusedResult]
    degraded: list[str]


def rrf(
    ranked_lists: Sequence[Sequence[uuid.UUID]], *, k: int = DEFAULT_RRF_K
) -> dict[uuid.UUID, float]:
    """Return ``chunk_id -> RRF score`` for any number of ranked id lists.

    Ranks are positions in each list, 1-based. A duplicate id within one list
    keeps its **best** (smallest) rank; the duplicate consumes its position, so
    the documents after it keep the ranks they actually had, and a malformed
    list cannot silently re-rank everything downstream of it. Ragged lists of
    different lengths and empty lists are both normal inputs.
    """
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        seen: set[uuid.UUID] = set()
        for rank, chunk_id in enumerate(ranked, start=1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def fuse(
    *,
    semantic: RetrievalOutcome,
    lexical: RetrievalOutcome,
    k: int,
    top_n: int,
) -> FusedResults:
    """Fuse the semantic and lexical outcomes into one ranked candidate list.

    ``top_n`` caps the fused pool that the reranker will see. Fused results are
    ordered by descending RRF score, with ties broken by best individual rank
    and then ``chunk_id``; ``final_rank`` is 1-based and dense over the returned
    (possibly capped) list.
    """
    semantic_ranks = _best_ranks(semantic.results)
    lexical_ranks = _best_ranks(lexical.results)
    scores = rrf([list(semantic_ranks), list(lexical_ranks)], k=k)
    sources = _select_sources(semantic.results, lexical.results)

    ordered_ids = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            _best_rank(semantic_ranks.get(chunk_id), lexical_ranks.get(chunk_id)),
            chunk_id,
        ),
    )
    ordered_ids = ordered_ids[:top_n] if top_n > 0 else []

    results = [
        FusedResult(
            chunk_id=chunk_id,
            rrf_score=scores[chunk_id],
            final_rank=final_rank,
            semantic_rank=semantic_ranks.get(chunk_id),
            lexical_rank=lexical_ranks.get(chunk_id),
            source=sources[chunk_id],
        )
        for final_rank, chunk_id in enumerate(ordered_ids, start=1)
    ]
    return FusedResults(
        results=results,
        degraded=_unique([*semantic.degraded, *lexical.degraded]),
    )


def _best_ranks(results: Sequence[SearchResult]) -> dict[uuid.UUID, int]:
    """Map each chunk to its best 1-based position, keeping the first occurrence."""
    ranks: dict[uuid.UUID, int] = {}
    for position, result in enumerate(results, start=1):
        ranks.setdefault(result.chunk_id, position)
    return ranks


def _select_sources(
    semantic_results: Sequence[SearchResult], lexical_results: Sequence[SearchResult]
) -> dict[uuid.UUID, SearchResult]:
    """Pick one ``SearchResult`` per chunk: the better-ranked one, semantic on a tie."""
    ranked: dict[uuid.UUID, tuple[int, SearchResult]] = {}
    for position, result in enumerate(semantic_results, start=1):
        ranked.setdefault(result.chunk_id, (position, result))
    for position, result in enumerate(lexical_results, start=1):
        existing = ranked.get(result.chunk_id)
        if existing is None or position < existing[0]:
            ranked[result.chunk_id] = (position, result)
    return {chunk_id: result for chunk_id, (_, result) in ranked.items()}


def _best_rank(semantic_rank: int | None, lexical_rank: int | None) -> int:
    """The smaller of two optional ranks; absent ranks sort last."""
    candidates = [rank for rank in (semantic_rank, lexical_rank) if rank is not None]
    return min(candidates) if candidates else 0


def _unique(reasons: Sequence[str]) -> list[str]:
    """Deduplicate degradation reasons while preserving their order."""
    seen: set[str] = set()
    unique: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique
