"""Unit tests for the multi-signal confidence model.

Everything under test here is a pure function, so these tests need no database
and no gateway. They assert the documented *direction* of each signal and the
documented bucket boundaries, including the two edges that are easy to get
wrong: a score exactly at the auto-accept threshold is accepted, and a score
exactly at the review floor is reviewed rather than discarded.
"""

from __future__ import annotations

import pytest

from coursellm.graph.confidence import (
    DEFAULT_WEIGHTS,
    GRAPH_AUTO_ACCEPT_CONFIDENCE,
    GRAPH_REVIEW_FLOOR_CONFIDENCE,
    ConfidenceWeights,
    EdgeDisposition,
    agreement_score,
    corroboration_score,
    cue_score,
    disposition_for,
    edge_confidence,
    score_edge,
)

pytestmark = pytest.mark.unit


class TestSignalDirection:
    def test_each_signal_moves_the_score_up(self) -> None:
        base = edge_confidence(0.0, 0.0, 0.0, 0.0)
        assert edge_confidence(0.8, 0.0, 0.0, 0.0) > base
        assert edge_confidence(0.0, 1.0, 0.0, 0.0) > base
        assert edge_confidence(0.0, 0.0, 1.0, 0.0) > base
        assert edge_confidence(0.0, 0.0, 0.0, 1.0) > base

    def test_llm_self_report_is_capped(self) -> None:
        capped = edge_confidence(1.0, 0.0, 0.0, 0.0)
        assert capped == pytest.approx(DEFAULT_WEIGHTS.base + DEFAULT_WEIGHTS.llm * 0.8)
        # A single self-confident assertion cannot reach auto-accept on its own.
        assert capped < GRAPH_AUTO_ACCEPT_CONFIDENCE
        assert capped == pytest.approx(0.35)

    def test_corroboration_is_saturating(self) -> None:
        assert corroboration_score(1) > 0.0
        assert corroboration_score(2) > corroboration_score(1)
        second_gain = corroboration_score(2) - corroboration_score(1)
        third_gain = corroboration_score(3) - corroboration_score(2)
        assert third_gain < second_gain

    def test_corroboration_beats_a_single_mention(self) -> None:
        single = score_edge(llm=0.8, distinct_documents=1, cue="explicit")
        corroborated = score_edge(llm=0.8, distinct_documents=3, cue="explicit")
        assert corroborated.score > single.score

    def test_explicit_cue_beats_an_inferred_one(self) -> None:
        assert cue_score("explicit") == pytest.approx(1.0)
        assert cue_score("inferred") == pytest.approx(0.0)
        assert cue_score("inferred", structural=True) == pytest.approx(0.4)
        explicit = score_edge(llm=0.8, distinct_documents=2, cue="explicit")
        inferred = score_edge(llm=0.8, distinct_documents=2, cue="inferred")
        assert explicit.score > inferred.score

    def test_agreement_ordering(self) -> None:
        verified = agreement_score(verified_same_relation=True)
        path = agreement_score(path_exists=True)
        none = agreement_score()
        assert verified > path > none
        # A contradiction against a human decision scores zero, never negative.
        assert agreement_score(verified_opposite=True) == pytest.approx(0.0)

    def test_unknown_cue_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown textual cue"):
            cue_score("guessed")


class TestBuckets:
    def test_explicit_thrice_corroborated_edge_auto_accepts(self) -> None:
        result = score_edge(llm=0.9, distinct_documents=3, cue="explicit")
        assert result.score >= GRAPH_AUTO_ACCEPT_CONFIDENCE
        assert result.disposition is EdgeDisposition.AUTO_ACCEPT

    def test_high_confidence_single_document_stays_in_review(self) -> None:
        result = score_edge(llm=1.0, distinct_documents=1, cue="explicit")
        assert result.disposition is EdgeDisposition.REVIEW

    def test_high_confidence_inferred_edge_stays_in_review(self) -> None:
        # With the cue term at zero the maximum attainable score is 0.75, so an
        # inferred edge cannot auto-accept even with unlimited corroboration.
        result = score_edge(llm=1.0, distinct_documents=5, cue="inferred")
        assert result.score < GRAPH_AUTO_ACCEPT_CONFIDENCE
        assert result.disposition is EdgeDisposition.REVIEW

    def test_low_score_is_discarded(self) -> None:
        result = score_edge(llm=0.3, distinct_documents=1, cue="inferred")
        assert result.score < GRAPH_REVIEW_FLOOR_CONFIDENCE
        assert result.disposition is EdgeDisposition.DISCARD

    def test_score_exactly_at_the_auto_accept_threshold_is_accepted(self) -> None:
        assert (
            disposition_for(GRAPH_AUTO_ACCEPT_CONFIDENCE, cue="explicit", supporting_documents=2)
            is EdgeDisposition.AUTO_ACCEPT
        )

    def test_score_exactly_at_the_review_floor_is_reviewed(self) -> None:
        assert (
            disposition_for(GRAPH_REVIEW_FLOOR_CONFIDENCE, cue="inferred", supporting_documents=1)
            is EdgeDisposition.REVIEW
        )
        assert (
            disposition_for(
                GRAPH_REVIEW_FLOOR_CONFIDENCE - 1e-9, cue="inferred", supporting_documents=1
            )
            is EdgeDisposition.DISCARD
        )

    def test_structural_cue_keeps_an_otherwise_auto_accepted_edge_in_review(self) -> None:
        # A score above the threshold but only a structural cue is not enough.
        assert (
            disposition_for(0.9, cue="inferred", supporting_documents=5) is EdgeDisposition.REVIEW
        )


class TestDeterminism:
    def test_same_signals_produce_the_same_score(self) -> None:
        first = score_edge(
            llm=0.7,
            distinct_documents=2,
            cue="explicit",
            verified_same_relation=True,
        )
        second = score_edge(
            llm=0.7,
            distinct_documents=2,
            cue="explicit",
            verified_same_relation=True,
        )
        assert first == second

    def test_score_is_clipped_to_the_unit_interval(self) -> None:
        assert edge_confidence(1.0, 1.0, 1.0, 1.0) <= 1.0
        assert edge_confidence(-5.0, -5.0, -5.0, -5.0) >= 0.0


class TestWeightValidation:
    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            ConfidenceWeights(base=0.5)

    def test_thresholds_must_be_ordered(self) -> None:
        with pytest.raises(ValueError, match="Thresholds must be ordered"):
            ConfidenceWeights(auto_accept=0.4, review_floor=0.6)

    def test_default_weights_are_the_documented_values(self) -> None:
        assert DEFAULT_WEIGHTS.total == pytest.approx(1.0)
        assert DEFAULT_WEIGHTS.llm_cap == pytest.approx(0.80)
        assert DEFAULT_WEIGHTS.auto_accept == pytest.approx(0.75)
        assert DEFAULT_WEIGHTS.review_floor == pytest.approx(0.50)
