# ADR-0005: Reciprocal Rank Fusion instead of normalised score summation

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0003, ADR-0006

## Context

The prototype combined its two retrievers with a weighted sum of min–max normalised
scores, in `coursellm/rag/retrieval/hybrid_search.py`:

```python
def _normalize(self, results, invert=False):
    scores = [r.score for r in results]
    min_score, max_score = min(scores), max(scores)
    for r in results:
        norm_score = 1.0 if max_score == min_score \
                     else (r.score - min_score) / (max_score - min_score)
        if invert:
            norm_score = 1.0 - norm_score
        ...

semantic_norm = self._normalize(semantic_raw, invert=True)
keyword_norm  = self._normalize(keyword_raw,  invert=False)
SEMANTIC_WEIGHT, KEYWORD_WEIGHT = 0.7, 0.3
```

The semantic retriever returns a **cosine distance** (lower is better:
`semantic_search.py` selects `cosine_distance(...)` and `ORDER BY distance`), so its
normalised list is inverted. The keyword retriever returns **`ts_rank`** (higher is
better), so its list is not inverted. The hybrid retriever fetches `top_k * 2` from
each side, so the pool normalisation sees is itself a function of `top_k`. Three
defects follow.

**1. Min–max normalisation is corpus-dependent.** Every score is rescaled by the
best and worst candidates *in that result set*. One outlier compresses every other
document's normalised score; changing `top_k` changes the pool and therefore the
`0.7`/`0.3` contribution of unchanged documents. The ranking of a fixed
(query, document) pair is not stable across requests, making results
non-reproducible and evaluation noisy.

**2. Cosine distance and `ts_rank` are not on comparable scales.** A fixed weight
between them is arbitrary: it does not transfer across queries, corpora, embedding
models, or a BM25 parameter change. `ts_rank` has no IDF, saturation, or length
normalisation (ADR-0003), so its distribution is compressed and corpus-dependent,
while cosine distance has different variance again. No calibration makes
`0.7 · cosine + 0.3 · ts_rank` meaningful.

**3. The inversion and accumulation are inconsistent.** The sign convention is
out-of-band: `_normalize` gets `invert=True` only because the caller knows which
retriever returns a distance, so a change of score convention silently inverts
fusion. The degenerate case is asymmetric: when all scores are equal,
`norm_score = 1.0` for every entry, so after inversion the semantic list contributes
`0.0` per document while an equally degenerate keyword list contributes `1.0`. The
merge also keeps no single consistent score: `final_score` accumulates while
`semantic_score` and `keyword_score` are overwritten by assignment, and the base
`score` field of the merged result is never populated, so downstream code reading
`.score` gets `None` and duplicates within one retriever desynchronise the
explanation fields from `final_score`.

## Decision

Fuse the two rank lists with **Reciprocal Rank Fusion (RRF)**:

```python
def rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, doc_id in enumerate(ranks, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
```

- Only **ranks** are used: no normalisation, no score scale, no inversion flag.
- A chunk in both lists receives both terms, so retriever agreement is rewarded; a
  chunk in one list keeps its single term.
- `k = 60` follows Cormack, Clarke, and Buettcher (2009), where it was near-optimal
  across collections; it is exposed as `RRF_K` because it interacts with list length.
- Per-retriever ranks are retained for explainability, and the RRF score is
  persisted in the trace alongside the reranker score.
- Lexical weights stay off by default (`HYBRID_LEXICAL_WEIGHT_ENABLED = false`); if
  enabled, RRF supports a per-list multiplier, chosen by measurement.

## Consequences

### Positive

- **Scale-free:** ranks are ordinal, so fusion is unaffected by whether a scorer
  emits distances, similarities, logits, or probabilities; no calibration is needed
  and no retriever dominates through an arbitrary scale.
- **Stable and reproducible:** adding or removing candidates does not rescale the
  contribution of documents already in the list.
- **Robust:** a single outlier score cannot distort the ranking, because magnitude
  is ignored by construction.
- **Standard and simple:** a handful of lines, unit-testable with no model or
  database, and it removes a class of bugs — no inversion flag, no `min == max`
  special case, no scale assumption.

### Negative

- **Per-retriever confidence is discarded.** Rank 1 with a decisive margin scores
  the same as a document that barely edged into rank 1; RRF cannot express that the
  dense retriever is more reliable for a given query.
- **A weak list still contributes.** If one retriever returns largely irrelevant
  results, their ranks still add score, promoting the least-bad document of a poor
  list; magnitude-aware fusion would suppress that.
- **Ties are common and arbitrary.** Near-collisions in `1/(k + rank)` must be
  broken deterministically (best individual rank, then chunk id) or the order is
  unstable.
- **`k` is another tuning knob.** Larger `k` flattens top-rank contribution, smaller
  `k` sharpens it; the value is a measurement and must be part of the configuration
  hash (ADR-0010).
- **Magnitude information the reranker could use is discarded** before reranking;
  the reranker compensates, but only over the fused candidate set.

### Neutral

- RRF runs after both retrievers have returned the same filtered, tenant-scoped
  candidate pool, so ranks are comparable in membership though not in scale.
- Every fused score decomposes into the two ranks that produced it, so fusion remains
  explainable.

## Alternatives considered

- **Weighted min–max sum (prototype).** Rejected for the three defects above.
- **Z-score or distribution-based normalisation with a weighted sum.** Removes the
  outlier sensitivity but still assumes comparable distributions and needs accurately
  estimated per-corpus statistics.
- **Convex combination of calibrated scores.** Principled, but it needs a calibration
  set and periodic recalibration per model and corpus; more machinery than the
  current scale justifies.
- **Learning to rank.** Strongest in principle and the likely eventual direction, but
  it needs labelled relevance data that does not yet exist at volume. Deferred until
  `evals/` can supply training data; revisitable without changing the retriever
  interfaces.

## How this is verified

- Unit tests on the fusion function with hand-computed ranks, including a chunk
  present in both lists (both terms summed) and one present in a single list.
- A stability test inserts a synthetic high-scoring outlier into one list and asserts
  the relative order of all pre-existing documents is unchanged. The prototype's
  normalisation fails this test; RRF passes it.
- `evals/` compares the fused list against the weighted-sum baseline on
  `evals/datasets/golden_rag.jsonl` using Recall@10 and MRR, with an ablation of each
  retriever alone. Figures are published in `evals/reports/`; none are asserted here.
- The `RRF_K` value used is part of the retrieval configuration hash recorded on the
  evaluation run (ADR-0010), so fused results are attributable to a configuration.
