"""The ranking stage: fuse, optionally rerank, order and truncate.

:func:`rank` composes the two pure-ish stages into the ordered passage list the
generator receives:

1. **Fuse** the semantic and lexical outcomes with Reciprocal Rank Fusion,
   capping the candidate pool at ``retrieval_top_k_per_retriever`` (the
   architecture document's N = 20).
2. **Rerank** the pool with the configured reranker, if enabled. A reranker that
   raises or exceeds ``rerank_timeout_ms`` degrades the request — the RRF order
   is kept and a machine-readable reason is recorded — because a slow or broken
   reranker must never fail the request.
3. **Apply the floor** ``rerank_min_score`` to the rerank scores. If the floor
   would remove every passage, the top passage is kept and
   ``rerank_floor_removed_all`` is recorded: an empty context is never a useful
   answer, and the caller must be able to see that the floor did the removing.
4. **Assign ``final_rank``** 1..n and truncate to ``rerank_top_k``.

``stage_latency_ms`` reports ``fusion`` and ``rerank`` separately. The prototype
timed its fusion stage to include the retrieval calls that produced its inputs,
which double-counted latency and made the fusion stage look like the bottleneck
it was not; here ``fusion`` covers only the RRF call.

No LLM is called anywhere in this module or this PR.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, replace

from coursellm.core.config import Settings
from coursellm.rag.fusion.rrf import FusedResult, fuse
from coursellm.rag.rerank.rerankers import RerankedResult, Reranker, get_reranker
from coursellm.rag.retrieval.types import RetrievalOutcome, SearchResult

#: The reranker raised, or produced no scores at all.
RERANKER_UNAVAILABLE = "reranker_unavailable"
#: The reranker exceeded ``rerank_timeout_ms``.
RERANK_TIMEOUT = "rerank_timeout"
#: ``rerank_min_score`` would have removed every passage.
RERANK_FLOOR_REMOVED_ALL = "rerank_floor_removed_all"


@dataclass(frozen=True, slots=True)
class RankedPassage:
    """One passage in the final order, with every score that produced it.

    ``rerank_score`` is ``None`` when reranking did not run (disabled, timed out
    or unavailable) and the passage's position comes from RRF alone.
    """

    chunk_id: uuid.UUID
    final_rank: int
    rrf_score: float
    rerank_score: float | None
    semantic_rank: int | None
    lexical_rank: int | None
    source: SearchResult


@dataclass(frozen=True, slots=True)
class RankingOutcome:
    """The ranked passages, any degradations, and per-stage latency."""

    results: list[RankedPassage]
    degraded: list[str]
    stage_latency_ms: dict[str, float]


async def rank(
    *,
    query: str,
    semantic: RetrievalOutcome,
    lexical: RetrievalOutcome,
    settings: Settings,
    reranker: Reranker | None = None,
) -> RankingOutcome:
    """Fuse, rerank and truncate two retriever outcomes into a ranking.

    ``reranker`` is injectable so tests can supply a deterministic double
    without constructing the cross-encoder. When it is omitted and reranking is
    enabled, :func:`coursellm.rag.rerank.rerankers.get_reranker` builds the
    configured one.
    """
    degraded: list[str] = []

    fusion_started = time.perf_counter()
    fused = fuse(
        semantic=semantic,
        lexical=lexical,
        k=settings.rrf_k,
        top_n=settings.retrieval_top_k_per_retriever,
    )
    fusion_ms = (time.perf_counter() - fusion_started) * 1000.0
    degraded.extend(fused.degraded)

    candidates = fused.results
    rerank_ms = 0.0
    reranked_ids: list[uuid.UUID] | None = None
    rerank_scores: dict[uuid.UUID, float] = {}

    if settings.rerank_enabled:
        active = reranker if reranker is not None else get_reranker(settings)
        rerank_started = time.perf_counter()
        try:
            reranked = await asyncio.wait_for(
                active.rerank(
                    query,
                    [candidate.source for candidate in candidates],
                    top_k=len(candidates),
                ),
                timeout=settings.rerank_timeout_ms / 1000.0,
            )
        except TimeoutError:
            degraded.append(RERANK_TIMEOUT)
        except Exception:
            degraded.append(RERANKER_UNAVAILABLE)
        else:
            if not reranked and candidates:
                # A reranker that returns nothing has failed, even if it did not
                # raise; falling back to RRF is safer than returning no context.
                degraded.append(RERANKER_UNAVAILABLE)
            else:
                reranked_ids, rerank_scores = _reranked_order(candidates, reranked)
        finally:
            rerank_ms = (time.perf_counter() - rerank_started) * 1000.0

    if reranked_ids is not None:
        ordered_ids = _apply_floor(reranked_ids, rerank_scores, settings, degraded)
        fused_by_id = {candidate.chunk_id: candidate for candidate in candidates}
        passages = [
            _to_passage(fused_by_id[chunk_id], rerank_scores.get(chunk_id))
            for chunk_id in ordered_ids
        ]
    else:
        passages = [_to_passage(candidate, None) for candidate in candidates]

    results = [
        replace(passage, final_rank=final_rank)
        for final_rank, passage in enumerate(passages[: settings.rerank_top_k], start=1)
    ]
    return RankingOutcome(
        results=results,
        degraded=degraded,
        stage_latency_ms={"fusion": fusion_ms, "rerank": rerank_ms},
    )


def _reranked_order(
    candidates: list[FusedResult],
    reranked: list[RerankedResult],
) -> tuple[list[uuid.UUID], dict[uuid.UUID, float]]:
    """Map the reranker's output back onto the fused candidates it was given.

    Only ids the reranker was actually handed are accepted, so a buggy reranker
    cannot invent a passage or duplicate one, and the fusion explanation
    (``rrf_score``, ``semantic_rank``, ``lexical_rank``) always comes from the
    fused result rather than from the reranker.
    """
    known = {candidate.chunk_id: candidate for candidate in candidates}
    order: list[uuid.UUID] = []
    scores: dict[uuid.UUID, float] = {}
    for item in reranked:
        if item.chunk_id in known and item.chunk_id not in scores:
            scores[item.chunk_id] = item.rerank_score
            order.append(item.chunk_id)
    return order, scores


def _apply_floor(
    order: list[uuid.UUID],
    scores: dict[uuid.UUID, float],
    settings: Settings,
    degraded: list[str],
) -> list[uuid.UUID]:
    """Drop passages below ``rerank_min_score``, never dropping all of them."""
    kept = [chunk_id for chunk_id in order if scores[chunk_id] >= settings.rerank_min_score]
    if kept or not order:
        return kept
    degraded.append(RERANK_FLOOR_REMOVED_ALL)
    return order[:1]


def _to_passage(candidate: FusedResult, rerank_score: float | None) -> RankedPassage:
    """Build a final passage with a placeholder rank that :func:`rank` fills in."""
    return RankedPassage(
        chunk_id=candidate.chunk_id,
        final_rank=0,
        rrf_score=candidate.rrf_score,
        rerank_score=rerank_score,
        semantic_rank=candidate.semantic_rank,
        lexical_rank=candidate.lexical_rank,
        source=candidate.source,
    )
