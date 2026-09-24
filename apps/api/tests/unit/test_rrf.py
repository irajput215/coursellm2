"""Reciprocal Rank Fusion, checked against hand-computed arithmetic.

Fusion is pure, so these tests build ``SearchResult`` objects by hand and assert
on the exact sums. The worked example below spells out every term so that a
change to the formula fails loudly rather than shifting a ranking subtly.
"""

from __future__ import annotations

import uuid
from typing import Literal

import pytest

from coursellm.db.models.content import SourceType
from coursellm.rag.fusion import DEFAULT_RRF_K, fuse, rrf
from coursellm.rag.fusion.rrf import FusedResults
from coursellm.rag.retrieval.types import RetrievalOutcome, SearchResult

pytestmark = pytest.mark.unit

Retriever = Literal["semantic", "lexical"]

# Deterministic ids. ``uuid.UUID(int=n)`` orders by ``n``, which is what the
# final tie-break relies on.
A = uuid.UUID(int=1)
B = uuid.UUID(int=2)
C = uuid.UUID(int=3)
D = uuid.UUID(int=4)


def _outcome(
    ids: list[uuid.UUID],
    *,
    retriever: Retriever = "semantic",
    degraded: list[str] | None = None,
) -> RetrievalOutcome:
    """A ranked outcome whose rows carry 1-based ranks and a placeholder score."""
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


# ---------------------------------------------------------------------------
# Worked example
# ---------------------------------------------------------------------------
def test_worked_two_list_example_scores_and_order() -> None:
    # semantic = [A, B, C, D], lexical = [B, C, A], k = 60.
    #
    #   1/61 = 0.016393442622950820
    #   1/62 = 0.016129032258064516
    #   1/63 = 0.015873015873015872
    #   1/64 = 0.015625
    #
    #   A: 1/61 + 1/63 = 0.016393442622950820 + 0.015873015873015872 = 0.032266458495966692
    #   B: 1/62 + 1/61 = 0.016129032258064516 + 0.016393442622950820 = 0.032522474881015336
    #   C: 1/63 + 1/62 = 0.015873015873015872 + 0.016129032258064516 = 0.032002048131080388
    #   D: 1/64        = 0.015625
    #
    # Order: B (0.032522...) > A (0.032266...) > C (0.032002...) > D (0.015625).
    fused = fuse(
        semantic=_outcome([A, B, C, D], retriever="semantic"),
        lexical=_outcome([B, C, A], retriever="lexical"),
        k=60,
        top_n=10,
    )

    scores = {result.chunk_id: result.rrf_score for result in fused.results}
    assert scores[A] == pytest.approx(1 / 61 + 1 / 63)
    assert scores[B] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[C] == pytest.approx(1 / 63 + 1 / 62)
    assert scores[D] == pytest.approx(1 / 64)

    assert [result.chunk_id for result in fused.results] == [B, A, C, D]
    assert [result.final_rank for result in fused.results] == [1, 2, 3, 4]

    by_id = {result.chunk_id: result for result in fused.results}
    assert (by_id[A].semantic_rank, by_id[A].lexical_rank) == (1, 3)
    assert (by_id[B].semantic_rank, by_id[B].lexical_rank) == (2, 1)
    assert (by_id[D].semantic_rank, by_id[D].lexical_rank) == (4, None)


def test_a_document_in_both_lists_beats_one_in_only_one() -> None:
    # B is in both lists (1/62 + 1/61 = 0.032522474881015336).
    # A is rank 1 in one list only (1/61 = 0.016393442622950820).
    # C is rank 2 in one list only (1/62 = 0.016129032258064516).
    fused = fuse(
        semantic=_outcome([A, B], retriever="semantic"),
        lexical=_outcome([B, C], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )
    scores = {result.chunk_id: result.rrf_score for result in fused.results}

    assert scores[B] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[B] > scores[A] > scores[C]
    assert fused.results[0].chunk_id == B


# ---------------------------------------------------------------------------
# The ``k`` constant
# ---------------------------------------------------------------------------
def test_larger_k_compresses_the_spread() -> None:
    def spread(k: int) -> float:
        scores = rrf([[A, B]], k=k)
        return scores[A] - scores[B]

    # spread(k) = 1/(k+1) - 1/(k+2) = 1 / ((k+1)(k+2)), decreasing in k.
    assert spread(1) == pytest.approx(1 / 6)
    assert spread(DEFAULT_RRF_K) == pytest.approx(1 / (61 * 62))
    assert spread(1) > spread(DEFAULT_RRF_K) > spread(1000)


def test_larger_k_pulls_the_rank_ratio_towards_one() -> None:
    def ratio(k: int) -> float:
        scores = rrf([[A, B]], k=k)
        return scores[A] / scores[B]

    assert ratio(1) == pytest.approx(3 / 2)
    assert ratio(DEFAULT_RRF_K) == pytest.approx(62 / 61)
    assert abs(ratio(1000) - 1.0) < abs(ratio(DEFAULT_RRF_K) - 1.0)


def test_default_k_is_sixty() -> None:
    assert DEFAULT_RRF_K == 60
    assert rrf([[A]]) == rrf([[A]], k=DEFAULT_RRF_K)


# ---------------------------------------------------------------------------
# Degenerate and malformed inputs
# ---------------------------------------------------------------------------
def test_empty_lists_score_nothing() -> None:
    assert rrf([]) == {}
    assert rrf([[], []]) == {}

    fused = fuse(
        semantic=_outcome([]),
        lexical=_outcome([], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )
    assert fused.results == []


def test_one_empty_list_degenerates_to_the_others_order() -> None:
    fused = fuse(
        semantic=_outcome([A, B, C], retriever="semantic"),
        lexical=_outcome([], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )

    assert [result.chunk_id for result in fused.results] == [A, B, C]
    assert [result.semantic_rank for result in fused.results] == [1, 2, 3]
    assert [result.lexical_rank for result in fused.results] == [None, None, None]
    by_id = {result.chunk_id: result for result in fused.results}
    assert by_id[A].rrf_score == pytest.approx(1 / 61)


def test_duplicate_ids_in_one_list_keep_the_best_rank() -> None:
    # [A, A, B]: A appears at ranks 1 and 2, so only rank 1 contributes:
    #   A = 1/61 = 0.016393442622950820 (not 1/61 + 1/62)
    # B keeps the position it actually had, rank 3, so a malformed list cannot
    # silently promote everything after a duplicate:
    #   B = 1/63 = 0.015873015873015872
    scores = rrf([[A, A, B]], k=60)

    assert scores[A] == pytest.approx(1 / 61)
    assert scores[B] == pytest.approx(1 / 63)
    assert scores[A] > scores[B]


def test_duplicate_across_lists_accumulates_both_terms() -> None:
    scores = rrf([[A], [A]], k=60)
    assert scores[A] == pytest.approx(2 / 61)


# ---------------------------------------------------------------------------
# Stability properties
# ---------------------------------------------------------------------------
def test_scores_are_independent_of_list_length() -> None:
    """Adding candidates must not rescale the score of a document already in a list."""
    short = rrf([[A, B]])
    long = rrf([[A, B, C, D]])

    assert short[A] == long[A]
    assert short[B] == long[B]
    assert long[C] == pytest.approx(1 / 63)
    assert long[D] == pytest.approx(1 / 64)


def test_an_outlier_appended_to_one_list_does_not_change_relative_order() -> None:
    before = rrf([[A, B, C]], k=60)
    after = rrf([[A, B, C, D]], k=60)

    before_order = sorted(before, key=lambda chunk_id: -before[chunk_id])
    after_order = [
        chunk_id for chunk_id in sorted(after, key=lambda cid: -after[cid]) if chunk_id != D
    ]

    assert before_order == after_order


def test_ties_are_broken_by_best_rank_then_chunk_id() -> None:
    # semantic = [A, B], lexical = [B, A]:
    #   A = 1/61 + 1/62, B = 1/62 + 1/61 -> exactly equal.
    # Both have best rank 1, so the ascending chunk_id decides: A (int=1) first.
    fused = fuse(
        semantic=_outcome([A, B], retriever="semantic"),
        lexical=_outcome([B, A], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )
    assert fused.results[0].rrf_score == pytest.approx(fused.results[1].rrf_score)
    assert [result.chunk_id for result in fused.results] == [A, B]

    # Swapping which list ranks which document first must not change the order.
    swapped = fuse(
        semantic=_outcome([B, A], retriever="semantic"),
        lexical=_outcome([A, B], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )
    assert [result.chunk_id for result in swapped.results] == [A, B]


# ---------------------------------------------------------------------------
# fuse(): caps, ranks, sources and degradations
# ---------------------------------------------------------------------------
def test_top_n_caps_the_candidate_pool() -> None:
    fused = fuse(
        semantic=_outcome([A, B, C, D]),
        lexical=_outcome([], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=2,
    )

    assert [result.chunk_id for result in fused.results] == [A, B]
    assert [result.final_rank for result in fused.results] == [1, 2]


def test_final_ranks_are_contiguous_from_one() -> None:
    fused = fuse(
        semantic=_outcome([A, B, C, D], retriever="semantic"),
        lexical=_outcome([D, C], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=10,
    )

    assert [result.final_rank for result in fused.results] == [1, 2, 3, 4]


def test_source_prefers_the_better_ranked_result() -> None:
    # A is rank 1 in both lists, so the semantic row is preferred on a tie.
    # B and C appear in one list each, so their own row is the only option.
    semantic = _outcome([A, B], retriever="semantic")
    lexical = _outcome([A, C], retriever="lexical")

    fused = fuse(semantic=semantic, lexical=lexical, k=DEFAULT_RRF_K, top_n=10)
    by_id = {result.chunk_id: result for result in fused.results}

    assert by_id[A].source is semantic.results[0]
    assert by_id[B].source is semantic.results[1]
    assert by_id[C].source is lexical.results[1]


def test_source_uses_the_lexical_row_when_it_ranks_the_chunk_better() -> None:
    semantic = _outcome([A, B], retriever="semantic")  # A rank 1, B rank 2
    lexical = _outcome([B, A], retriever="lexical")  # B rank 1, A rank 2

    fused = fuse(semantic=semantic, lexical=lexical, k=DEFAULT_RRF_K, top_n=10)
    by_id = {result.chunk_id: result for result in fused.results}

    assert by_id[B].source is lexical.results[0]
    assert by_id[B].source.retriever == "lexical"


def test_retriever_degradations_are_merged_and_deduplicated() -> None:
    fused = fuse(
        semantic=_outcome([A], degraded=["semantic_unavailable"]),
        lexical=_outcome([B], retriever="lexical", degraded=["lexical_unavailable"]),
        k=DEFAULT_RRF_K,
        top_n=10,
    )

    assert fused.degraded == ["semantic_unavailable", "lexical_unavailable"]
    assert isinstance(fused, FusedResults)


def test_top_n_of_zero_returns_no_candidates() -> None:
    fused = fuse(
        semantic=_outcome([A, B]),
        lexical=_outcome([], retriever="lexical"),
        k=DEFAULT_RRF_K,
        top_n=0,
    )
    assert fused.results == []
