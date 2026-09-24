"""Citation metrics: precision, recall and hallucination rate.

A generated answer cites ids like ``[S1]``. Each id either resolves to a passage
that was actually offered in the context (valid), or it does not
(hallucinated). A hallucinated citation is worse than no citation, because it
claims support that does not exist and survives a reader's spot check.

The metrics are defined over three sets:

* ``cited`` — the ids the answer cites, in first-appearance order;
* ``offered`` — every id that was placed in the context;
* ``relevant_offered`` — the subset of offered ids whose source document is in
  the golden entry's expected sources.

Then:

``citation_precision   = |cited INTERSECT offered| / |cited|``
``citation_recall      = |cited INTERSECT offered INTERSECT relevant_offered|
                          / |relevant_offered|``
``hallucination_rate   = |cited - offered| / |cited|``

Every denominator of zero yields ``0.0``: an answer that cites nothing makes no
citation claim, and the generator's own refusal rule (a refusal cites nothing
and is not a precision failure) is applied upstream, not here.
"""

from __future__ import annotations

from collections.abc import Iterable, Set
from dataclasses import dataclass

from coursellm.rag.generation.citations import verify_citations


@dataclass(frozen=True, slots=True)
class CitationMetrics:
    """Citation precision, recall and hallucination rate for one answer."""

    precision: float
    recall: float
    hallucination_rate: float
    cited: int
    valid: int
    hallucinated: int


def citation_metrics(
    *,
    cited: Iterable[str],
    offered: Set[str],
    relevant_offered: Set[str],
) -> CitationMetrics:
    """Compute the three citation metrics from id sets.

    ``cited`` is de-duplicated. ``offered`` is every citation id placed in the
    context, and ``relevant_offered`` is the subset of those whose document is
    an expected source. A cited id that is not offered is hallucinated even if
    it happens to be in ``relevant_offered``.
    """
    cited_list = list(dict.fromkeys(cited))
    cited_set = set(cited_list)
    offered_set = set(offered)
    relevant_set = set(relevant_offered)

    valid = cited_set & offered_set
    hallucinated = cited_set - offered_set

    precision = len(valid) / len(cited_set) if cited_set else 0.0
    recall = len(valid & relevant_set) / len(relevant_set) if relevant_set else 0.0
    rate = len(hallucinated) / len(cited_set) if cited_set else 0.0
    return CitationMetrics(
        precision=precision,
        recall=recall,
        hallucination_rate=rate,
        cited=len(cited_set),
        valid=len(valid),
        hallucinated=len(hallucinated),
    )


def citation_metrics_from_answer(
    text: str,
    *,
    offered: Set[str],
    relevant_offered: Set[str],
) -> CitationMetrics:
    """Compute the metrics from raw answer text using the production parser.

    Delegates extraction to
    :func:`coursellm.rag.generation.citations.verify_citations`, so the
    evaluation and the generator agree on what a citation is (``[S1]``,
    ``[S1][S2]`` and ``[S1, S2]`` are all recognised) and cannot drift apart.
    """
    report = verify_citations(text, offered)
    return citation_metrics(
        cited=report.cited,
        offered=offered,
        relevant_offered=relevant_offered,
    )


__all__ = [
    "CitationMetrics",
    "citation_metrics",
    "citation_metrics_from_answer",
]
