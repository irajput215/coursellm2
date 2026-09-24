# ADR-0003: Hybrid retrieval with BM25 and dense vectors

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0002, ADR-0005

## Context

The prototype had two retrievers of very different quality. `semantic_search.py`
orders chunks by `Chunk.embedding.cosine_distance(query_embedding)`. The lexical
path, `keyword_search.py`, ranks with PostgreSQL's `ts_rank`:

```sql
SELECT *, ts_rank(to_tsvector('english', content),
                  plainto_tsquery('english', :question)) AS keyword_score
FROM chunks
WHERE user_id = :user_id
  AND to_tsvector('english', content) @@ plainto_tsquery('english', :question)
ORDER BY keyword_score DESC LIMIT :top_k
```

`ts_rank` is **not BM25**, and the three missing properties matter individually:

1. **No term-frequency saturation.** BM25's
   `f(q,d)·(k1+1) / (f(q,d) + k1·(1−b+b·|d|/avgdl))` is concave in term frequency,
   so the tenth occurrence adds far less than the first. A `ts_rank`-style sum keeps
   rewarding repetition roughly linearly, so a long document that repeats a term can
   outrank a short, precise one.
2. **No document-length normalisation.** BM25's `b` term divides by a length factor;
   `ts_rank` has none, so longer documents are systematically favoured simply for
   containing more terms. `ts_rank_cd` adds cover-density normalisation but is still
   not BM25.
3. **No IDF in the Okapi sense.** BM25 weights each term by
   `ln(1 + (N − n(q) + 0.5)/(n(q) + 0.5))`, so rare terms dominate. `ts_rank` has no
   comparable inverse-document-frequency term, so a query about a rare identifier is
   not separated from one about a common word.

Dense-only retrieval fails in the complementary way. Embeddings are excellent at
meaning and poor at identifiers: for `HNSW`, `ef_construction`, `BCEWithLogitsLoss`,
`CUDA_VISIBLE_DEVICES`, `eq. 4.2`, an error code, a standard number, or `v1.5`
versus `v1.4`, subword tokenisation splits the string, pooling averages it away
against the rest of the passage, and near-miss strings (`ef_search`,
`ef_construction`) can sit close in vector space. A dense list may not contain the
exact-term document at all, at any rank. Because dense and lexical errors are not
strongly correlated, fusing them beats either alone.

## Decision

Retrieve with **both** a dense and a lexical retriever over the same tenant-scoped,
filter-resolved candidate set, then fuse by rank (ADR-0005).

- **Dense:** pgvector HNSW cosine ANN, top-20 (`RETRIEVAL_TOP_K_PER_RETRIEVER`).
- **Lexical:** explicit **Okapi BM25**, `k1 = 1.2`, `b = 0.75`, in the standard form
  `score(d) = Σ IDF(q_i) · f(q_i,d)(k1+1) / (f(q_i,d) + k1(1 − b + b·|d|/avgdl))`,
  with `IDF(q_i) = ln(1 + (N − n(q_i) + 0.5)/(n(q_i) + 0.5))`.
- **Storage:** `chunk_terms(chunk_id, tenant_id, term, tf)`,
  `tenant_lexical_stats(tenant_id, term, doc_freq)`, and
  `tenant_corpus_stats(tenant_id, doc_count, avg_doc_len, total_tokens)`. A GIN
  index on a generated `tsvector` selects candidates by index; BM25 then rescores
  only those candidates.
- **Analysis:** index-time and query-time analysis are identical (lowercasing,
  stopword removal, light `english` stemming), so term sets are comparable.
- **Statistics are per tenant**, computed only over the corpus a tenant may
  retrieve; global IDF would distort scoring and leak other tenants' vocabulary.
- **Both retrievers receive the same resolved filters**, pushed down rather than
  applied after fusion, so the two rank lists describe the same eligible set.

Query expansion (HyDE, multi-query) is off by default behind a config flag, enabled
only when an evaluation run shows a recall gain that justifies the added latency
and variance.

## Consequences

### Positive

- Exact-term queries are recoverable: identifiers, acronyms, error codes, and
  equation labels are ranked by a scorer that rewards the rare literal.
- Rare-term IDF weighting and length normalisation remove two systematic biases
  `ts_rank` introduced.
- The retrievers cover each other's misses — BM25 anchors literal tokens, the dense
  path recovers paraphrases and synonyms.
- Each retriever degrades independently: if the vector path errors, lexical
  retrieval still answers and the response is marked `lexical only`, and vice versa
  (`docs/architecture/system.md` §6).
- Per-tenant statistics keep scores comparable over the retrievable corpus and
  remove a subtle cross-tenant information leak.

### Negative

- Two queries per request plus fusion add latency and a failure surface; each path
  must time out and degrade independently.
- BM25 statistics are derived state. Inserts, updates, and deletes must maintain
  `chunk_terms`, `tenant_lexical_stats`, and `tenant_corpus_stats`; drift silently
  degrades scores and needs an ingestion hook, a reconciliation job, and a drift
  metric.
- Storage and index maintenance grow roughly with corpus length, and `doc_freq` must
  be recomputed as the corpus changes.
- Tuning surface grows (`k1`, `b`, per-retriever `top_k`, analysis configuration),
  and any analysis change invalidates term statistics and requires a lexical
  reindex.
- Index-time/query-time tokenisation mismatch produces plausible-but-wrong rankings
  rather than an error.

### Neutral

- BM25 and vectors are complements, not competitors; neither is treated as primary.
- Lexical weights are off by default (`HYBRID_LEXICAL_WEIGHT_ENABLED = false`);
  fusion is rank-based, and weights enter only if an evaluation run shows they help.
- Stemming is deliberately light: aggressive stemming improves recall but damages
  identifier matching, one reason lexical retrieval exists.

## Alternatives considered

- **Keep `ts_rank` (prototype).** Least work, but it lacks saturation, length
  normalisation, and Okapi IDF, so it is not a defensible lexical baseline.
  Rejected.
- **Dense-only.** Simplest, often adequate for prose, but it predictably fails
  identifier-heavy questions, which are common in technical course material.
  Rejected.
- **Lexical-only.** Good for exact terms, poor for paraphrase and conceptual
  questions, and it inherits vocabulary-mismatch failures. Rejected.
- **Learned sparse retrieval (SPLADE and similar).** Strong hybrid behaviour in one
  model, but it needs a term-expansion model at index and query time, a much larger
  inverted index, and extra serving infrastructure. Deferred; re-evaluable against
  the same harness.
- **A hosted search engine for the lexical side.** Capable, but it adds a second
  datastore and a synchronisation path; the BM25 implementation is small enough to
  own (ADR-0002).

## How this is verified

- Unit tests score a hand-computed fixture against the BM25 formula and assert
  saturation, length normalisation, and IDF behaviour directly, with no model.
- `evals/` reports Recall@20 for the dense retriever, Recall@20 for BM25, and
  Recall@10/MRR for the fused list on `evals/datasets/golden_rag.jsonl`, plus an
  identifier-heavy slice that isolates the lexical contribution. Figures are
  published in `evals/reports/`; none are asserted here.
- A regression test asserts a query containing an exact identifier returns the
  document defining it within the fused top-k.
- An ingestion test asserts BM25 statistics update on insert, update, and delete,
  and a reconciliation job compares recomputed with stored statistics and reports
  drift.
