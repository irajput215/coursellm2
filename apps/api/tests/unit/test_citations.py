"""Citation extraction, verification and hallucination stripping.

Precision and recall are pinned to hand-computed fractions, and the
empty-citation rule is pinned for both a refusal and an ordinary answer, because
that rule is a policy choice rather than arithmetic.
"""

from __future__ import annotations

import pytest

from coursellm.rag.generation.citations import (
    CitationReport,
    bounded_quote,
    extract_citation_ids,
    is_refusal,
    strip_hallucinated,
    verify_citations,
)

pytestmark = pytest.mark.unit


def test_three_marker_forms_are_extracted_in_first_appearance_order() -> None:
    text = "One [S1]. Two [S2][S3]. Three [S4, S5]. Again [S1]."

    assert extract_citation_ids(text) == ["S1", "S2", "S3", "S4", "S5"]


def test_ids_are_deduplicated() -> None:
    assert extract_citation_ids("[S2][S2] and [S2, S2]") == ["S2"]


def test_non_citation_brackets_are_ignored() -> None:
    assert extract_citation_ids("See [Figure 1] and [S1].") == ["S1"]


def test_hallucinated_ids_are_reported() -> None:
    report = verify_citations("Grounded [S1]. Invented [S9].", {"S1", "S2"})

    assert isinstance(report, CitationReport)
    assert report.cited == ["S1", "S9"]
    assert report.valid == ["S1"]
    assert report.hallucinated == ["S9"]
    assert report.unused == ["S2"]


def test_precision_and_recall_are_hand_computed() -> None:
    # cited = S1, S9; valid = S1; available = S1..S4.
    report = verify_citations("A [S1] B [S9]", {"S1", "S2", "S3", "S4"})

    assert report.citation_precision == pytest.approx(1 / 2)
    assert report.citation_recall == pytest.approx(1 / 4)
    assert report.unused == ["S2", "S3", "S4"]


def test_a_refusal_without_citations_scores_precision_one() -> None:
    report = verify_citations(
        "I don't have enough information in the course materials to answer that.",
        {"S1", "S2"},
    )

    assert report.cited == []
    assert report.citation_precision == 1.0
    assert report.citation_recall == 0.0


def test_an_uncited_answer_scores_precision_zero() -> None:
    report = verify_citations("The derivative measures the rate of change.", {"S1"})

    assert report.cited == []
    assert report.citation_precision == 0.0


def test_valid_ids_can_be_a_strict_subset_of_available() -> None:
    report = verify_citations("A [S1] B [S2]", {"S1", "S2"}, valid_ids={"S1"})

    assert report.valid == ["S1"]
    assert report.hallucinated == []
    assert report.citation_precision == pytest.approx(1 / 2)


def test_strip_hallucinated_removes_only_unknown_markers() -> None:
    stripped, removed = strip_hallucinated("Prose [S1] and invented [S9] here.", {"S1"})

    assert "[S1]" in stripped
    assert "[S9]" not in stripped
    assert "Prose" in stripped
    assert stripped.endswith("here.")
    assert removed == ["S9"]


def test_strip_hallucinated_keeps_real_ids_in_a_mixed_group() -> None:
    stripped, removed = strip_hallucinated("Claim [S1, S9] continues.", {"S1"})

    assert stripped == "Claim [S1] continues."
    assert removed == ["S9"]


def test_strip_hallucinated_leaves_non_citation_brackets_alone() -> None:
    stripped, removed = strip_hallucinated("See [Figure 1] and [S9].", {"S1"})

    assert "[Figure 1]" in stripped
    assert "[S9]" not in stripped
    assert removed == ["S9"]


def test_strip_hallucinated_deduplicates_removed_ids() -> None:
    _stripped, removed = strip_hallucinated("[S9] then [S9]", {"S1"})

    assert removed == ["S9"]


def test_is_refusal_recognises_common_phrasings() -> None:
    assert is_refusal("I don't have enough information to answer that.")
    assert is_refusal("There is insufficient evidence in the sources.")
    assert is_refusal("I cannot answer that from the provided material.")
    assert not is_refusal("Self attention mixes information across positions [S1].")


class TestBoundedQuote:
    """The API bound applied to every citation, from both engines and history."""

    def test_a_short_span_is_whitespace_collapsed(self) -> None:
        assert bounded_quote("  attention   weights\ninputs  ") == "attention weights inputs"

    def test_a_long_span_is_trimmed_to_a_sentence_boundary(self) -> None:
        first = "Attention mixes information across positions. "
        text = first + "The rest of this passage is deliberately much longer " * 8
        quote = bounded_quote(text, limit=120)
        assert len(quote) <= 120
        assert quote == first.strip()

    def test_a_span_with_no_sentence_boundary_is_cut_and_marked(self) -> None:
        quote = bounded_quote("x" * 500, limit=100)
        assert len(quote) == 100  # at most the limit, ellipsis included
        assert quote.endswith("...")

    def test_the_default_limit_is_240(self) -> None:
        assert len(bounded_quote("word " * 200)) <= 240

    def test_citation_response_applies_the_bound_for_every_engine(self) -> None:
        from uuid import uuid4

        from coursellm.api.schemas.chat import CitationResponse

        response = CitationResponse(
            citation_id="S1",
            chunk_id=uuid4(),
            document_id=uuid4(),
            filename="notes.txt",
            page=1,
            source_type="document",
            quote="x" * 500,
        )
        assert len(response.quote) <= 240
