"""The multi-signal confidence model for LLM-asserted edges.

A language model asked for relationships will produce them, including when the
text does not support them, and its self-reported confidence is systematically
overconfident on exactly the relations that matter most. An asserted edge is
therefore treated as **evidence to be corroborated**, never as a fact
(``docs/architecture/knowledge-graph.md`` §6).

Four signals are combined by a weighted sum:

===============  =====  =====================================================
Signal           Weight  Source
===============  =====  =====================================================
base              0.15  a constant floor: no single signal is ever certain
LLM self-report   0.25  capped at ``GRAPH_LLM_CONFIDENCE_CAP`` (0.8)
corroboration     0.30  ``1 - exp(-distinct_documents / 2)`` — saturating
cue               0.20  1.0 explicit, 0.4 structural, 0.0 inferred
agreement         0.10  1.0 verified same, 0.7 non-contradicting path, else 0.0
===============  =====  =====================================================

Everything in this module is a pure function of its arguments. There is no
session, no settings lookup and no clock, which is what makes the confidence
model unit-testable and deterministic: the same signals always produce the same
score and therefore the same disposition.

The weights and thresholds live in :class:`ConfidenceWeights` because the
architecture document requires the application to validate at startup that the
five weights sum to 1.0 — a drifted weight set silently changes which edges are
traversable. ``coursellm.core.config.Settings`` does not yet expose the
``GRAPH_W_*`` fields (see the PR report); this dataclass is the single source of
truth until it does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

#: Ceiling applied to the model's own confidence. A self-confident single
#: assertion must not be able to cross the auto-accept threshold alone.
GRAPH_LLM_CONFIDENCE_CAP = 0.80
#: Auto-accept requires the score, an explicit cue *and* two supporting documents.
GRAPH_AUTO_ACCEPT_CONFIDENCE = 0.75
#: Below this, an edge is discarded rather than queued for review.
GRAPH_REVIEW_FLOOR_CONFIDENCE = 0.50
#: Below this, an edge is excluded from traversal and from answers.
GRAPH_MIN_TRAVERSABLE_CONFIDENCE = 0.60
#: Structural cue strength (a heading hierarchy implies a relation).
GRAPH_STRUCTURAL_CUE = 0.40
#: Agreement with an existing non-contradicting path.
GRAPH_PATH_AGREEMENT = 0.70

_WEIGHT_SUM_TOLERANCE = 1e-9

Cue = Literal["explicit", "inferred"]


@dataclass(frozen=True, slots=True)
class ConfidenceWeights:
    """The weight set and the thresholds it is calibrated against.

    Frozen and validated on construction: an invalid weight set is a programming
    error that should surface at import, not a silent change in which edges are
    traversable.
    """

    base: float = 0.15
    llm: float = 0.25
    corroboration: float = 0.30
    cue: float = 0.20
    agreement: float = 0.10
    llm_cap: float = GRAPH_LLM_CONFIDENCE_CAP
    auto_accept: float = GRAPH_AUTO_ACCEPT_CONFIDENCE
    review_floor: float = GRAPH_REVIEW_FLOOR_CONFIDENCE
    min_traversable: float = GRAPH_MIN_TRAVERSABLE_CONFIDENCE

    def __post_init__(self) -> None:
        if abs(self.total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            msg = (
                "The knowledge-graph confidence weights must sum to 1.0; "
                f"got {self.total!r}. A drifted weight set silently changes which "
                f"edges are traversable."
            )
            raise ValueError(msg)
        if not 0.0 <= self.review_floor <= self.auto_accept <= 1.0:
            msg = (
                "Thresholds must be ordered 0 <= review_floor <= auto_accept <= 1; "
                f"got review_floor={self.review_floor!r}, auto_accept={self.auto_accept!r}."
            )
            raise ValueError(msg)
        if not 0.0 <= self.min_traversable <= 1.0:
            msg = f"min_traversable must be in [0, 1]; got {self.min_traversable!r}."
            raise ValueError(msg)

    @property
    def total(self) -> float:
        return self.base + self.llm + self.corroboration + self.cue + self.agreement


#: The documented defaults. Constructed at import so a drifted set fails loudly.
DEFAULT_WEIGHTS = ConfidenceWeights()


class EdgeDisposition(StrEnum):
    """What the pipeline does with a scored edge."""

    AUTO_ACCEPT = "auto_accept"
    REVIEW = "review"
    DISCARD = "discard"


@dataclass(frozen=True, slots=True)
class EdgeScore:
    """The components, the final score and the resulting disposition."""

    llm: float
    corroboration: float
    cue: float
    agreement: float
    score: float
    disposition: EdgeDisposition
    supporting_documents: int


def corroboration_score(distinct_documents: int) -> float:
    """``1 - exp(-distinct_documents / 2)``.

    Saturating by design: the third independent source adds less than the
    second, so a single chatty document cannot manufacture agreement.
    """
    if distinct_documents <= 0:
        return 0.0
    return 1.0 - math.exp(-distinct_documents / 2.0)


def cue_score(cue: str, *, structural: bool = False) -> float:
    """Explicit beats structurally implied beats pure inference."""
    if cue == "explicit":
        return 1.0
    if cue == "inferred":
        return GRAPH_STRUCTURAL_CUE if structural else 0.0
    msg = f"Unknown textual cue {cue!r}; expected 'explicit' or 'inferred'."
    raise ValueError(msg)


def agreement_score(
    *,
    verified_same_relation: bool = False,
    path_exists: bool = False,
    verified_opposite: bool = False,
) -> float:
    """Consistency with the existing graph, in the documented order.

    An edge that contradicts a human-verified edge scores zero rather than
    negative: the confidence model never *punishes* a claim, it declines to
    support one. The pipeline handles the contradiction separately by queueing it
    for review.
    """
    if verified_opposite and not verified_same_relation:
        return 0.0
    if verified_same_relation:
        return 1.0
    if path_exists:
        return GRAPH_PATH_AGREEMENT
    return 0.0


def edge_confidence(
    llm: float,
    corroboration: float,
    cue: float,
    agreement: float,
    *,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> float:
    """Weighted multi-signal confidence, clipped to ``[0, 1]``."""
    raw = (
        weights.base * 1.0
        + weights.llm * min(max(llm, 0.0), weights.llm_cap)
        + weights.corroboration * min(max(corroboration, 0.0), 1.0)
        + weights.cue * min(max(cue, 0.0), 1.0)
        + weights.agreement * min(max(agreement, 0.0), 1.0)
    )
    return max(0.0, min(1.0, raw))


def disposition_for(
    score: float,
    *,
    cue: Cue,
    supporting_documents: int,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> EdgeDisposition:
    """Bucket a score per §6.2.

    A high score is *not* sufficient for auto-acceptance: the edge must also rest
    on an explicit cue and be corroborated by at least two documents. A
    confident-sounding single mention is a review item.
    """
    if score < weights.review_floor:
        return EdgeDisposition.DISCARD
    if score >= weights.auto_accept and cue == "explicit" and supporting_documents >= 2:
        return EdgeDisposition.AUTO_ACCEPT
    return EdgeDisposition.REVIEW


def score_edge(
    *,
    llm: float,
    distinct_documents: int,
    cue: Cue,
    structural: bool = False,
    verified_same_relation: bool = False,
    path_exists: bool = False,
    verified_opposite: bool = False,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> EdgeScore:
    """Score one edge from raw signals. Deterministic and pure."""
    corroboration = corroboration_score(distinct_documents)
    cue_value = cue_score(cue, structural=structural)
    agreement = agreement_score(
        verified_same_relation=verified_same_relation,
        path_exists=path_exists,
        verified_opposite=verified_opposite,
    )
    score = edge_confidence(llm, corroboration, cue_value, agreement, weights=weights)
    return EdgeScore(
        llm=min(max(llm, 0.0), weights.llm_cap),
        corroboration=corroboration,
        cue=cue_value,
        agreement=agreement,
        score=score,
        disposition=disposition_for(
            score,
            cue=cue,
            supporting_documents=distinct_documents,
            weights=weights,
        ),
        supporting_documents=distinct_documents,
    )


__all__ = [
    "DEFAULT_WEIGHTS",
    "GRAPH_AUTO_ACCEPT_CONFIDENCE",
    "GRAPH_LLM_CONFIDENCE_CAP",
    "GRAPH_MIN_TRAVERSABLE_CONFIDENCE",
    "GRAPH_REVIEW_FLOOR_CONFIDENCE",
    "ConfidenceWeights",
    "EdgeDisposition",
    "EdgeScore",
    "agreement_score",
    "corroboration_score",
    "cue_score",
    "disposition_for",
    "edge_confidence",
    "score_edge",
]
