---
title: Okapi BM25 scoring
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Okapi BM25 scoring

Okapi BM25 is a bag-of-words ranking function. For a document `d` and a query
with terms `t`, it sums a per-term contribution:

```text
score(d) = SUM over query terms t of
             IDF(t) * ( tf(t, d) * (k1 + 1) )
             / ( tf(t, d) + k1 * (1 - b + b * len(d) / avgdl) )
```

`tf(t, d)` is the number of times term `t` occurs in `d`, `len(d)` is the
document length, and `avgdl` is the mean document length in the corpus.

## Term-frequency saturation and `k1`

The numerator grows linearly in `tf`, but the denominator also contains `tf`, so
the contribution approaches a ceiling as a term repeats. This is **term
frequency saturation**: the tenth occurrence of a word adds much less than the
second. `k1` sets how quickly saturation sets in. A larger `k1` lets term
frequency keep mattering for longer; a smaller `k1` saturates sooner. The
commonly used default is `k1 = 1.2`.

## Length normalisation and `b`

The `b` parameter controls how strongly document length is normalised. With
`b = 0` there is no length normalisation; with `b = 1` the effect is full. The
purpose is to stop long documents from outranking short ones merely because they
contain more chances to repeat a query term. The commonly used default is
`b = 0.75`.

## Inverse document frequency

IDF down-weights terms that occur in many documents and up-weights rare terms.
A widely used non-negative form is:

```text
IDF(t) = ln( 1 + (N - df(t) + 0.5) / (df(t) + 0.5) )
```

where `N` is the number of documents in the corpus and `df(t)` is the number of
documents containing `t`. The constant `1` inside the logarithm keeps the value
positive even for a term that appears in every document; the classic
Robertson-Sparck-Jones form without it can go negative, which would make a
frequent term actively penalise a match.

## What BM25 scores do and do not mean

BM25 is unbounded above, and its magnitude depends on the query, the corpus size
and the length statistics. Scores from two different queries are not comparable,
and a BM25 score is not comparable to a cosine similarity. Fusion that combines
retrievers should therefore use ranks rather than raw scores.

CourseLLM computes BM25 explicitly rather than using PostgreSQL `ts_rank`, which
has no term-frequency saturation, no document-length normalisation, and no
corpus-level IDF. It materialises per-chunk term frequencies and per-tenant
document frequencies, and treats each chunk as the unit that `N` counts: a
retrieval result is a chunk, so the corpus over which IDF is computed is the
chunk set the tenant is allowed to retrieve.
