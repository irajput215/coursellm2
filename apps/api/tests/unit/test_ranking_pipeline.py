"""The ranking pipeline: fusion, reranking, the score floor and degradation.

The pipeline needs no database — it is pure given two
:class:`~coursellm.rag.retrieval.types.RetrievalOutcome` objects — so it lives
in the unit suite rather than the integration suite. The integration package's
autouse ``clean_db`` fixture would otherwise force a PostgreSQL round trip onto
tests that never touch a database. Rerankers are injected doubles, so no model
is ever downloaded.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

import pytest

from coursellm.core.config import Settings
from coursellm.db.models.content import SourceType
from coursellm.rag.rerank import pipeline as pipeline_module
from coursellm.rag.rerank.pipeline import (
    RERANK_FLOOR_REMOVED_ALL,
    RERANK_TIMEOUT,
    RERANKER_UNAVAILABLE,
    RankingOutcome,
    rank,
)
from coursellm.rag.rerank.rerankers import RerankedResult
from coursellm.rag.retrieval.types import RetrievalOutcome, SearchResult

pytestmark = pytest.mark.unit

Retriever = Literal["semantic", "lexical"]

A = uuid.UUID(int=1)
B = uuid.UUID(int=2)
C = uuid.UUID(int=3)
D = uuid.UUID(int=4)


def _outcome(
    ids: list[uuid.UUID],
    retriever: Retriever = "semantic",
    *,
    degraded: list[str] | None = None,
) -> RetrievalOutcome:
    results = [
        SearchResult(
            chunk_id=chunk_id,
            document_id=uuid.UUID(int=100),
            course_id=uuid.UUID(int=200),
            content=f"passage {chunk_id.int}",
            page=1,
            topic=None,
            token_count=5,
            source_type=SourceType.LECTURE,
            rank=rank,
            score=1.0 - rank / 100.0,
            retriever=retriever,
        )
        for rank, chunk_id in enumerate(ids, start=1)
    ]
    return RetrievalOutcome(
        results=results,
        degraded=list(degraded or []),
        latency_ms=1.0,
        retriever=retriever,
    )


def _enabled(settings: Settings, **overrides: object) -> Settings:
    update: dict[str, object] = {"rerank_enabled": True, **overrides}
    return settings.model_copy(update=update)


class _ScriptedReranker:
    """A deterministic reranker double: scores come from a mapping."""

    model_id = "scripted"

    def __init__(self, scores: dict[uuid.UUID, float]) -> None:
        self._scores = scores
        self.calls = 0

    async def rerank(
        self, query: str, results: list[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        self.calls += 1
        scored = [
            (self._scores.get(result.chunk_id, 0.0), position, result)
            for position, result in enumerate(results, start=1)
        ]
        scored.sort(key=lambda item: (-item[0], item[2].chunk_id))
        return [
            RerankedResult(
                chunk_id=result.chunk_id,
                rerank_score=round(score, 6),
                original_rank=position,
                new_rank=new_rank,
                source=result,
            )
            for new_rank, (score, position, result) in enumerate(scored[:top_k], start=1)
        ]


class _FailingReranker:
    model_id = "failing"

    async def rerank(
        self, query: str, results: list[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        msg = "the model exploded"
        raise RuntimeError(msg)


class _SlowReranker:
    model_id = "slow"

    async def rerank(
        self, query: str, results: list[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        await asyncio.sleep(5)
        return []


class _EmptyReranker:
    model_id = "empty"

    async def rerank(
        self, query: str, results: list[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        return []


# ---------------------------------------------------------------------------
# Fusion-only path
# ---------------------------------------------------------------------------
async def test_disabled_reranking_keeps_the_fusion_order(test_settings: Settings) -> None:
    # semantic = [A, B, C], lexical = [B, A]:
    #   A = 1/61 + 1/62, B = 1/62 + 1/61 (tie), C = 1/63.
    # The tie breaks on chunk_id, so the fused order is A, B, C.
    reranker = _ScriptedReranker({B: 99.0})
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([B, A], "lexical"),
        settings=test_settings,
        reranker=reranker,
    )

    assert isinstance(outcome, RankingOutcome)
    assert reranker.calls == 0, "reranking is disabled, so the reranker must not be called"
    assert [result.chunk_id for result in outcome.results] == [A, B, C]
    assert [result.final_rank for result in outcome.results] == [1, 2, 3]
    assert [result.rerank_score for result in outcome.results] == [None, None, None]
    assert outcome.degraded == []
    assert outcome.stage_latency_ms["rerank"] == 0.0


async def test_retriever_degradations_reach_the_outcome(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([], "semantic", degraded=["semantic_unavailable"]),
        lexical=_outcome([A], "lexical"),
        settings=test_settings,
    )

    assert outcome.degraded == ["semantic_unavailable"]
    assert [result.chunk_id for result in outcome.results] == [A]


async def test_empty_candidates_yield_an_empty_ranking(test_settings: Settings) -> None:
    reranker = _ScriptedReranker({})
    outcome = await rank(
        query="q",
        semantic=_outcome([], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings),
        reranker=reranker,
    )

    assert outcome.results == []
    assert outcome.degraded == [], "nothing to rerank is not a reranker failure"


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------
async def test_reranker_reorders_and_records_scores(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings),
        reranker=_ScriptedReranker({A: -1.0, B: 5.0, C: 2.0}),
    )

    assert [result.chunk_id for result in outcome.results] == [B, C, A]
    assert [result.rerank_score for result in outcome.results] == [5.0, 2.0, -1.0]
    assert [result.final_rank for result in outcome.results] == [1, 2, 3]
    assert outcome.degraded == []


async def test_stage_latency_has_separate_keys(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B], "semantic"),
        lexical=_outcome([B], "lexical"),
        settings=_enabled(test_settings),
        reranker=_ScriptedReranker({A: 1.0, B: 2.0}),
    )

    assert set(outcome.stage_latency_ms) == {"fusion", "rerank"}
    assert all(value >= 0.0 for value in outcome.stage_latency_ms.values())
    assert outcome.stage_latency_ms["rerank"] > 0.0


async def test_truncates_to_rerank_top_k(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C, D], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings, rerank_top_k=2),
        reranker=_ScriptedReranker({A: 4.0, B: 3.0, C: 2.0, D: 1.0}),
    )

    assert [result.chunk_id for result in outcome.results] == [A, B]
    assert [result.final_rank for result in outcome.results] == [1, 2]


async def test_final_ranks_are_contiguous_from_one(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C, D], "semantic"),
        lexical=_outcome([D, C, B], "lexical"),
        settings=_enabled(test_settings),
        reranker=_ScriptedReranker({A: 1.0, B: 4.0, C: 3.0, D: 2.0}),
    )

    assert [result.final_rank for result in outcome.results] == [1, 2, 3, 4]


async def test_fusion_explanation_survives_reranking(test_settings: Settings) -> None:
    semantic = _outcome([A, B, C], "semantic")
    lexical = _outcome([B, A], "lexical")
    outcome = await rank(
        query="q",
        semantic=semantic,
        lexical=lexical,
        settings=_enabled(test_settings),
        reranker=_ScriptedReranker({A: 1.0, B: 3.0, C: 2.0}),
    )
    by_id = {result.chunk_id: result for result in outcome.results}

    assert (by_id[A].semantic_rank, by_id[A].lexical_rank) == (1, 2)
    assert (by_id[B].semantic_rank, by_id[B].lexical_rank) == (2, 1)
    assert (by_id[C].semantic_rank, by_id[C].lexical_rank) == (3, None)
    assert by_id[A].source is semantic.results[0]
    assert by_id[A].rrf_score > 0.0


async def test_rank_builds_the_configured_reranker_when_none_is_injected(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = _ScriptedReranker({A: 0.0, B: 1.0, C: 2.0})
    monkeypatch.setattr(pipeline_module, "get_reranker", lambda settings: scripted)

    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings),
    )

    assert [result.chunk_id for result in outcome.results] == [C, B, A]


# ---------------------------------------------------------------------------
# The rerank_min_score floor
# ---------------------------------------------------------------------------
async def test_floor_drops_passages_below_min_score(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings, rerank_min_score=0.0),
        reranker=_ScriptedReranker({A: 2.0, B: -1.0, C: 1.0}),
    )

    assert [result.chunk_id for result in outcome.results] == [A, C]
    assert [result.final_rank for result in outcome.results] == [1, 2]
    assert RERANK_FLOOR_REMOVED_ALL not in outcome.degraded


async def test_floor_that_removes_everything_keeps_the_top_passage(
    test_settings: Settings,
) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings, rerank_min_score=100.0),
        reranker=_ScriptedReranker({A: 2.0, B: 1.0, C: 0.5}),
    )

    assert [result.chunk_id for result in outcome.results] == [A]
    assert [result.final_rank for result in outcome.results] == [1]
    assert outcome.degraded == [RERANK_FLOOR_REMOVED_ALL]


# ---------------------------------------------------------------------------
# Degradation: a broken reranker never fails the request
# ---------------------------------------------------------------------------
async def test_reranker_exception_keeps_the_fusion_order(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([B, A], "lexical"),
        settings=_enabled(test_settings),
        reranker=_FailingReranker(),
    )

    assert [result.chunk_id for result in outcome.results] == [A, B, C]
    assert [result.rerank_score for result in outcome.results] == [None, None, None]
    assert outcome.degraded == [RERANKER_UNAVAILABLE]


async def test_reranker_timeout_keeps_the_fusion_order(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([B, A], "lexical"),
        settings=_enabled(test_settings, rerank_timeout_ms=50),
        reranker=_SlowReranker(),
    )

    assert [result.chunk_id for result in outcome.results] == [A, B, C]
    assert [result.rerank_score for result in outcome.results] == [None, None, None]
    assert outcome.degraded == [RERANK_TIMEOUT]
    assert outcome.stage_latency_ms["rerank"] < 1000.0


async def test_empty_reranker_output_is_treated_as_unavailable(test_settings: Settings) -> None:
    outcome = await rank(
        query="q",
        semantic=_outcome([A, B, C], "semantic"),
        lexical=_outcome([B, A], "lexical"),
        settings=_enabled(test_settings),
        reranker=_EmptyReranker(),
    )

    assert [result.chunk_id for result in outcome.results] == [A, B, C]
    assert outcome.degraded == [RERANKER_UNAVAILABLE]


async def test_a_reranker_cannot_invent_or_duplicate_passages(test_settings: Settings) -> None:
    class _MischievousReranker:
        model_id = "mischievous"

        async def rerank(
            self, query: str, results: list[SearchResult], *, top_k: int
        ) -> list[RerankedResult]:
            ghost = results[0].chunk_id if results else uuid.UUID(int=999)
            return [
                RerankedResult(ghost, 9.0, 1, 1, results[0]),
                RerankedResult(ghost, 8.0, 1, 2, results[0]),
                RerankedResult(uuid.UUID(int=999), 7.0, 1, 3, results[0]),
            ]

    outcome = await rank(
        query="q",
        semantic=_outcome([A, B], "semantic"),
        lexical=_outcome([], "lexical"),
        settings=_enabled(test_settings),
        reranker=_MischievousReranker(),
    )

    assert [result.chunk_id for result in outcome.results] == [A]
    assert [result.final_rank for result in outcome.results] == [1]
