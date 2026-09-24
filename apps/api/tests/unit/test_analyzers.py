"""Analyser tests.

The analyser is the contract between indexing and retrieval, so the tests here
are about *identity* (the same input always yields the same terms, and the query
path cannot drift from the index path) more than about linguistic quality.
"""

from __future__ import annotations

import pytest

from coursellm.rag.analyzers import (
    normalize_query,
    term_frequencies,
    tokenize,
    tokenize_for_index,
)

pytestmark = pytest.mark.unit


class TestIdentifiersSurvive:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("ef_construction", ["ef_construction"]),
            ("bge-reranker-base", ["bge-reranker-base"]),
            ("gpt-4o", ["gpt-4o"]),
            ("c++", ["c++"]),
            ("BCEWithLogitsLoss", ["bcewithlogitsloss"]),
            ("4.2", ["4.2"]),
        ],
    )
    def test_identifier_is_a_single_term(self, text: str, expected: list[str]) -> None:
        assert tokenize(text) == expected

    def test_identifier_survives_inside_a_sentence(self) -> None:
        assert tokenize("Use ef_construction and HNSW.") == [
            "use",
            "ef_construction",
            "and",
            "hnsw",
        ]

    def test_trailing_sentence_punctuation_is_not_part_of_the_term(self) -> None:
        assert tokenize("see eq. 4.2 in the paper") == ["see", "eq", "4.2", "in", "the", "paper"]


class TestNormalisation:
    def test_deterministic(self) -> None:
        text = "HNSW indexes use ef_construction 200."
        assert tokenize(text) == tokenize(text)

    def test_lowercased(self) -> None:
        assert tokenize("HNSW") == ["hnsw"]

    def test_nfkc_folds_full_width_characters(self) -> None:
        # Full-width "4.2"; escapes keep the source free of ambiguous characters.
        assert tokenize("\uff14\uff0e\uff12") == ["4.2"]

    def test_non_alphanumeric_is_a_separator(self) -> None:
        assert tokenize("hello, world!") == ["hello", "world"]

    def test_punctuation_only_text_has_no_terms(self) -> None:
        assert tokenize("... --- +++ !!!") == []


class TestStopwordsAndQuerySymmetry:
    def test_index_analysis_removes_stopwords(self) -> None:
        assert tokenize_for_index("the quick brown fox") == ["quick", "brown", "fox"]

    def test_all_stopwords_is_empty_for_indexing(self) -> None:
        assert tokenize_for_index("the and of to") == []

    def test_query_and_index_agree_on_content_terms(self) -> None:
        text = "the HNSW index uses ef_construction for recall"
        assert normalize_query(text) == tokenize_for_index(text)

    def test_stopword_only_query_still_returns_terms(self) -> None:
        assert normalize_query("the and of") == ["the", "and", "of"]
        assert normalize_query("the and of") != []

    def test_normalize_query_keeps_identifiers(self) -> None:
        assert normalize_query("what is ef_construction?") == ["ef_construction"]


class TestTermFrequencies:
    def test_counts_occurrences(self) -> None:
        assert term_frequencies(["a", "b", "a", "a"]) == {"a": 3, "b": 1}

    def test_empty(self) -> None:
        assert term_frequencies([]) == {}

    def test_matches_tokenize_for_index_output(self) -> None:
        tokens = tokenize_for_index("the cat sat on the mat")
        assert term_frequencies(tokens) == {"cat": 1, "sat": 1, "mat": 1}
