"""Native retrieval metrics, implemented as pure functions over ids.

Nothing here depends on RAGAS or on any network call. Every function takes an
ordered sequence of retrieved ids and a set of relevant ids, and returns a
number. That makes each one hand-computable in a unit test, which is the only
way to be sure a metric means what its name says.

Two conventions are used throughout and are stated once here rather than
repeated in every docstring:

* **An empty relevant set is not a perfect result.** ``recall_at_k``,
  ``context_recall`` and ``ndcg_at_k`` return ``0.0`` when nothing is relevant.
  The alternative — treating "nothing to find" as 1.0 — lets an unanswerable
  question inflate an average. Callers that want to exclude unanswerable
  questions must exclude them explicitly, which is what the runner does.
* **Ids are opaque.** The functions never assume the ids are integers or that
  they are contiguous; they only test membership.
"""

from __future__ import annotations

import math
from collections.abc import Sequence, Set


def recall_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    """Fraction of relevant ids present in the first ``k`` retrieved ids.

    ``recall@k = |retrieved[:k] INTERSECT relevant| / |relevant|``.

    Degenerate cases: an empty relevant set returns ``0.0`` (there is no recall
    to define, and 1.0 would reward an empty question); ``k <= 0`` or an empty
    retrieved list returns ``0.0``; ``k`` larger than the list uses the whole
    list; a single retrieved relevant id returns ``1.0``.
    """
    if not relevant:
        return 0.0
    top = retrieved[:k] if k > 0 else []
    return len(set(top) & set(relevant)) / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    """Fraction of the first ``k`` retrieved ids that are relevant.

    ``precision@k = |retrieved[:k] INTERSECT relevant| / |retrieved[:k]|``.

    Degenerate cases: ``k <= 0`` or an empty retrieved list returns ``0.0``;
    ``k`` larger than the list divides by the actual list length, not by ``k``,
    so a short list is not penalised for being short; no relevant ids returns
    ``0.0`` unless nothing was retrieved, in which case the answer is also 0.0.
    """
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    return len(set(top) & set(relevant)) / len(top)


def mrr(retrieved: Sequence[str], relevant: Set[str], *, k: int | None = None) -> float:
    """Reciprocal rank of the first relevant id, or ``0.0`` if there is none.

    ``MRR = 1 / rank`` where ``rank`` is the 1-based position of the first
    relevant id in ``retrieved`` (optionally only within the first ``k``).
    A relevant id at rank 1 scores ``1.0``; at rank 4 it scores ``0.25``.
    """
    window = retrieved if k is None else retrieved[: max(k, 0)]
    for rank, item in enumerate(window, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    """Binary-gain normalised discounted cumulative gain at ``k``.

    Relevance is binary, so ``gain`` is 1 for a relevant id and 0 otherwise:

    ``DCG@k  = SUM over positions i=1..k of rel(i) / log2(i + 1)``
    ``IDCG@k = SUM over positions i=1..min(|relevant|, k) of 1 / log2(i + 1)``
    ``nDCG@k = DCG@k / IDCG@k``.

    Degenerate cases: an empty relevant set or ``k <= 0`` returns ``0.0``; a
    perfect ranking returns ``1.0``; ``k`` larger than the list uses the whole
    list for DCG while IDCG stays bounded by ``min(|relevant|, k)``.
    """
    if not relevant or k <= 0:
        return 0.0
    top = retrieved[:k]
    dcg = sum(
        1.0 / math.log2(position + 1) for position, item in enumerate(top, 1) if item in relevant
    )
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal > 0.0 else 0.0


def context_precision(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    """RAGAS context precision over the first ``k`` retrieved chunks.

    For each position ``i`` that holds a relevant chunk, take the precision at
    that position, ``precision@i = |retrieved[:i] INTERSECT relevant| / i``, and
    average those values over the relevant chunks that were retrieved:

    ``context_precision = (SUM over relevant retrieved i of precision@i)
                           / |relevant retrieved within k|``.

    This rewards putting relevant chunks early. It returns ``0.0`` when no
    relevant chunk was retrieved within ``k`` or when ``k <= 0``. Placing two
    relevant chunks at ranks 1 and 3 of three gives ``(1/1 + 2/3) / 2 = 5/6``.
    """
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    running_relevant = 0
    total = 0.0
    relevant_retrieved = 0
    for position, item in enumerate(top, start=1):
        if item in relevant:
            running_relevant += 1
            total += running_relevant / position
            relevant_retrieved += 1
    if relevant_retrieved == 0:
        return 0.0
    return total / relevant_retrieved


def context_recall(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    """RAGAS context recall: fraction of relevant chunks that were retrieved.

    ``context_recall = |retrieved[:k] INTERSECT relevant| / |relevant|``.

    It is the same arithmetic as :func:`recall_at_k`; it is named separately
    because it is reported as a context metric alongside
    :func:`context_precision`. An empty relevant set returns ``0.0`` for the
    reason given in the module docstring.
    """
    return recall_at_k(retrieved, relevant, k)


__all__ = [
    "context_precision",
    "context_recall",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]
