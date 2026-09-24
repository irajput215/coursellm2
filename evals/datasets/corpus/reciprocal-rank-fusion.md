---
title: Reciprocal rank fusion
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Reciprocal rank fusion

Reciprocal rank fusion (RRF) combines several ranked lists into one ranked
list using only the positions of the items. For a document `d`, the score is

```text
score(d) = SUM over rank lists L that contain d of 1 / (k + rank_L(d))
```

where `rank_L(d)` is the document's 1-based position in list `L` and `k` is a
constant. The original paper by Cormack, Clarke and Buettcher (2009) reports
`k = 60` as a good default. A document that appears in several lists accumulates
one term per list, so agreement between retrievers is rewarded. A document that
appears in only one list keeps a single term.

## Why rank-based fusion

RRF was designed for lists whose scores are not comparable. A cosine similarity
in `[-1, 1]` and a BM25 score, which is unbounded above, cannot be added with
fixed weights without assuming the two distributions are commensurate. That
assumption does not hold across queries, corpora, embedding models, or a change
to a BM25 parameter. Ranks are comparable by construction.

The price is explicit: **RRF discards per-retriever confidence**. The rank-1
document and the rank-2 document contribute `1/(k+1)` and `1/(k+2)`
respectively, regardless of how large the score gap between them was. A list in
which the top result is a near-tie still contributes almost as much as a list in
which the top result is decisive. Magnitude information is thrown away before
any downstream stage sees it, which is why a reranking stage that rescores the
fused candidates is useful.

## Noise and ties

A document buried deep in one list still contributes a small term
(`1/(k + rank)`), so RRF is sensitive to long weak lists. In practice the lists
are truncated to a top-N before fusion so that a large contribution cannot come
from a position no one would call relevant.

`1 / (k + rank)` values collide often, especially when the same ranks recur
across lists. A fusion implementation must therefore define a total, reproducible
tie-break. CourseLLM breaks ties by the best individual rank and then by the
document identifier, so the fused order is identical across runs for identical
inputs. That determinism is what makes a fused ranking comparable between two
evaluation runs and therefore what makes a regression gate meaningful.
