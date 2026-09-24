"""Pure-Python BM25 reference and the properties the SQL implementation relies on.

The integration suite asserts that the SQL in
:mod:`coursellm.rag.retrieval.lexical` produces *these* numbers for a fixed
corpus; this module defines the reference formula and pins the mathematical
properties the query depends on. Keeping the reference pure Python means the
properties can be checked with no database at all, which is the point: if the
formula is wrong, the SQL-vs-reference test would agree with it and be worthless.

    score(d) = SUM over query terms t of
                 IDF(t) * ( tf(t, d) * (k1 + 1) )
                 / ( tf(t, d) + k1 * (1 - b + b * len(d) / avgdl) )
    IDF(t) = ln( 1 + (N - df(t) + 0.5) / (df(t) + 0.5) )
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import pytest

pytestmark = pytest.mark.unit


def idf(doc_count: int, doc_freq: int) -> float:
    """Okapi BM25's probabilistic IDF, with the +1 inside the logarithm.

    The ``+1`` is what keeps the value non-negative for a term that occurs in
    every document: without it, ``ln((N - N + 0.5) / (N + 0.5))`` is negative and
    a ubiquitous term would *penalise* a document rather than contribute nothing.
    """
    return math.log(1.0 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))


def bm25_score(
    *,
    query_terms: Iterable[str],
    term_frequencies: Mapping[str, int],
    token_count: int,
    doc_frequencies: Mapping[str, int],
    doc_count: int,
    avg_doc_len: float,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    """The reference score, matching the SQL's arithmetic term for term.

    Query terms are de-duplicated because the SQL joins ``chunk_terms`` (one row
    per ``(chunk, term)``) and filters with ``term IN (...)``: a repeated query
    term matches the same one row once, so it must contribute once here too.
    """
    score = 0.0
    for term in sorted(set(query_terms)):
        tf = term_frequencies.get(term, 0)
        if tf == 0:
            continue
        df = doc_frequencies[term]
        denominator = tf + k1 * (1.0 - b + b * token_count / avg_doc_len)
        score += idf(doc_count, df) * (tf * (k1 + 1.0)) / denominator
    return score


# ---------------------------------------------------------------------------
# IDF shape
# ---------------------------------------------------------------------------
def test_idf_is_higher_for_a_rarer_term() -> None:
    assert idf(100, 1) > idf(100, 50) > idf(100, 99)


def test_idf_is_positive_and_decreasing_in_document_frequency() -> None:
    values = [idf(100, df) for df in range(1, 101)]
    assert all(value > 0.0 for value in values)
    assert values == sorted(values, reverse=True)


def test_term_in_every_document_has_near_zero_idf() -> None:
    """A ubiquitous term must not dominate a query by itself."""
    everywhere = idf(100, 100)
    assert 0.0 < everywhere < 0.01
    assert everywhere < idf(100, 90) < idf(100, 50)


# ---------------------------------------------------------------------------
# Term-frequency saturation and length normalisation
# ---------------------------------------------------------------------------
def test_term_frequency_saturates() -> None:
    common = {
        "query_terms": ["alpha"],
        "token_count": 100,
        "doc_frequencies": {"alpha": 10},
        "doc_count": 100,
        "avg_doc_len": 100.0,
    }
    once = bm25_score(term_frequencies={"alpha": 1}, **common)  # type: ignore[arg-type]
    twice = bm25_score(term_frequencies={"alpha": 2}, **common)  # type: ignore[arg-type]

    assert twice > once
    assert twice < 2.0 * once


def test_longer_documents_score_lower_for_equal_term_frequency() -> None:
    base = {
        "query_terms": ["alpha"],
        "term_frequencies": {"alpha": 1},
        "doc_frequencies": {"alpha": 10},
        "doc_count": 100,
        "avg_doc_len": 100.0,
    }
    short = bm25_score(token_count=100, **base)  # type: ignore[arg-type]
    long = bm25_score(token_count=300, **base)  # type: ignore[arg-type]

    assert long < short


def test_k1_zero_degenerates_to_a_pure_idf_weighted_count() -> None:
    """With ``k1 = 0`` the saturation term cancels, leaving one IDF per term."""
    expected = idf(100, 1) + idf(100, 50)
    first = bm25_score(
        query_terms=["alpha", "beta"],
        term_frequencies={"alpha": 1, "beta": 1},
        token_count=10,
        doc_frequencies={"alpha": 1, "beta": 50},
        doc_count=100,
        avg_doc_len=10.0,
        k1=0.0,
    )
    second = bm25_score(
        query_terms=["alpha", "beta"],
        term_frequencies={"alpha": 9, "beta": 3},
        token_count=900,
        doc_frequencies={"alpha": 1, "beta": 50},
        doc_count=100,
        avg_doc_len=10.0,
        k1=0.0,
    )

    assert first == pytest.approx(expected)
    assert second == pytest.approx(expected)


def test_b_zero_disables_length_normalisation() -> None:
    base = {
        "query_terms": ["alpha"],
        "term_frequencies": {"alpha": 1},
        "doc_frequencies": {"alpha": 10},
        "doc_count": 100,
        "avg_doc_len": 100.0,
        "b": 0.0,
    }
    short = bm25_score(token_count=10, **base)  # type: ignore[arg-type]
    long = bm25_score(token_count=1000, **base)  # type: ignore[arg-type]

    assert short == pytest.approx(long)


# ---------------------------------------------------------------------------
# Worked examples
# ---------------------------------------------------------------------------
def test_worked_example_rare_term() -> None:
    # N=100, df=1, tf=1, len=10, avgdl=10:
    #   IDF = ln(1 + (99.5 / 1.5)) = ln(67.3333...) = 4.209655...
    #   denominator = 1 + 1.2 * (0.25 + 0.75 * 1.0) = 2.2
    #   score = 4.209655... * (1 * 2.2) / 2.2 = 4.209655...
    score = bm25_score(
        query_terms=["rare"],
        term_frequencies={"rare": 1},
        token_count=10,
        doc_frequencies={"rare": 1},
        doc_count=100,
        avg_doc_len=10.0,
    )
    assert score == pytest.approx(math.log(1.0 + 99.5 / 1.5))
    assert score == pytest.approx(4.209655, abs=1e-6)


def test_worked_example_ubiquitous_term() -> None:
    # N=100, df=100: IDF = ln(1 + 0.5 / 100.5) = ln(1.0049751...) = 0.0049628...
    score = bm25_score(
        query_terms=["everywhere"],
        term_frequencies={"everywhere": 1},
        token_count=10,
        doc_frequencies={"everywhere": 100},
        doc_count=100,
        avg_doc_len=10.0,
    )
    assert score == pytest.approx(math.log(1.0 + 0.5 / 100.5))
    assert score < 0.01


def test_repeated_query_terms_contribute_once() -> None:
    kwargs = {
        "term_frequencies": {"alpha": 2},
        "token_count": 10,
        "doc_frequencies": {"alpha": 5},
        "doc_count": 100,
        "avg_doc_len": 10.0,
    }
    once = bm25_score(query_terms=["alpha"], **kwargs)  # type: ignore[arg-type]
    twice = bm25_score(query_terms=["alpha", "alpha"], **kwargs)  # type: ignore[arg-type]

    assert once == pytest.approx(twice)


def test_absent_query_term_contributes_nothing() -> None:
    score = bm25_score(
        query_terms=["alpha", "missing"],
        term_frequencies={"alpha": 1},
        token_count=10,
        doc_frequencies={"alpha": 5, "missing": 100},
        doc_count=100,
        avg_doc_len=10.0,
    )
    alpha_only = bm25_score(
        query_terms=["alpha"],
        term_frequencies={"alpha": 1},
        token_count=10,
        doc_frequencies={"alpha": 5},
        doc_count=100,
        avg_doc_len=10.0,
    )

    assert score == pytest.approx(alpha_only)
