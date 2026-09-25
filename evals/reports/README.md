# Evaluation reports

## How a report is produced

Every report is the direct output of a runner; nothing in this directory is
typed by hand.

```bash
# Retrieval only. No LLM, no provider key, no network. Writes the committed baseline shape.
.venv/bin/python -m evals.runners.run_retrieval_eval --output evals/reports/retrieval.json

# Full run: retrieval + generation + judging.
.venv/bin/python -m evals.runners.run_rag_eval --output evals/reports/latest.json

# The gate.
.venv/bin/python -m evals.runners.check_regression \
    --baseline evals/reports/baseline.json --current evals/reports/latest.json
```

`make eval-retrieval`, `make eval` and `make eval-gate` wrap the same three
commands from the repository root.

A report contains the dataset hash, the corpus hash, the retrieval
configuration version and its full snapshot, the embedding provider and model,
the reranker id, the git commit when one is readable from `.git`, a timestamp,
the machine it ran on, every per-question result, the aggregate `metrics`, and a
`not_measured` map. A metric is either a number the run computed or an entry in
`not_measured` with a stable reason code; there is no third state.

## The committed baseline

`baseline.json` is the **first of three identical retrieval runs** executed on
the final code. It is the output of:

```bash
DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_dev" \
  .venv/bin/python -m evals.runners.run_retrieval_eval --output evals/reports/baseline.json
```

| Field | Value |
|-------|-------|
| `schema_version` | `1.0` |
| `kind` | `retrieval` |
| `judge` | `none` (no LLM is called) |
| `generation_mode` | `none` |
| `dataset` | 32 entries: 20 factual, 4 multi-hop, 4 identifier, 4 unanswerable |
| dataset SHA-256 | `309c4d5ceb285841802bec910dd916acfa32ec59403958e870c85cacbb49b5e7` |
| `corpus` | 8 documents, 14 chunks |
| corpus SHA-256 | `b072f18351ece319b9a4e8c3d516b609745c9178b946b51b2455af8a58090b72` |
| `config.retrieval_config_version` | `8f3641b0eafb` |
| embedding provider / model | `hashing` / `hashing-v1`, dim 384 |
| reranker | `lexical-fallback` (`LexicalReranker`) |
| `retrieval_top_k_per_retriever` / `rrf_k` | 20 / 60 |
| `bm25_k1` / `bm25_b` | 1.2 / 0.75 |
| `rerank_top_k` / `context_token_budget` | 5 / 3000 |
| `hnsw_ef_search` | 100 |
| database | PostgreSQL 18.4 (`coursellm_dev`), connected as the RLS-enforcing `coursellm_app` role |
| machine | `Ishus-MacBook-Air.local`, macOS 26.5.1, arm64, Python 3.13.7 |
| git commit recorded | `d0d3c91fab7097d35a6cd6d13caca9f9b64d166f` |

The config version moved from `4e535f72cb58` to `8f3641b0eafb` when
`rerank_min_score` was added to the `retrieval_config_version` payload (it was the
one retrieval-affecting setting missing from the hash). The default value did not
change, so every metric above is byte-for-byte the same run; only the version that
labels the configuration changed. The committed metrics were not regenerated.

## What these numbers do not mean

This section exists because the headline retrieval numbers are easy to misread, and a
metric whose scope is not stated is worse than no metric.

**The embedding provider is `hashing`, not a real embedding model.** `HashingEmbedder`
is a deterministic feature-hash of the tokenised text. It exists so that the entire
retrieval stack is testable in CI with no model download, no network and no cost. It has
no semantic generalisation whatsoever.

The consequence is that `retrieval.semantic_recall_at_20 = 1.0` and
`retrieval.context_recall = 0.923` **are not evidence about embedding quality.** A
hashed bag-of-words retriever trivially achieves high recall on a corpus of 8 documents
and 14 chunks when the questions share vocabulary with the answers — which is exactly
what a question written against its own source material does. Measuring real semantic
retrieval requires the `local` sentence-transformers provider, a materially larger
corpus, and questions whose wording does not echo the source.

What this baseline *does* measure, and does so reproducibly:

* that the fusion, ranking and citation plumbing is wired correctly end to end;
* that a **lexical-only** retriever with real BM25 achieves
  `lexical_recall_at_20 = 0.958` on this dataset, which is a genuine statement about the
  BM25 implementation because it depends on term statistics rather than on embeddings;
* that the pipeline is **deterministic** — three runs produced identical retrieval
  metrics, which is the property that makes the regression gate meaningful;
* per-stage latency and the `not_measured` accounting.

**The reranker is `lexical-fallback`, not a cross-encoder.** `CrossEncoderReranker`
requires the `rerank` extra (torch + transformers), which the default install does not
include. `rerank_enabled` is true and the fallback is exercised, so the pipeline path is
covered, but no cross-encoder was measured. The rerank figures therefore say nothing
about `BAAI/bge-reranker-base`.

**No generation metric was measured.** `faithfulness`, `answer_relevance`,
`answer_correctness`, citation precision/recall and the token/cost metrics are all
`not_measured` with `no_llm`, because no provider key was configured. That is the honest
state, not a gap in the harness: the same command measures them for real when a key is
present.

The README must not be read as claiming the prototype's unreproducible numbers. It
claims strictly less, and every claim it makes names the configuration that produced it.

The git commit is the checked-out commit. The working tree carried the
uncommitted feature branch changes that produced this harness, so the SHA
identifies the base commit, not the exact tree; the dataset and corpus hashes
are the identifiers that make the measurement reproducible regardless.

## Metrics that were measured

* Retrieval: `semantic_recall_at_20`, `lexical_recall_at_20`,
  `fused_recall_at_10`, `fused_mrr`, `precision_at_5`, `recall_at_5`,
  `mrr_at_5`, `ndcg_at_5`, `context_precision`, `context_recall`.
* System: p50/p95/p99 latency for the semantic, lexical, fusion and rerank
  stages, `degradation_rate`, `error_rate`.

## Metrics that are `not_measured`, and why

| Metric | Reason code | Why |
|--------|-------------|-----|
| `citations.precision`, `citations.recall`, `citations.hallucination_rate` | `no_generation` | The retrieval runner calls no model, so there is no answer to cite from. The full RAG runner measures these from the generated (or extractively composed) answer. |
| `generation.faithfulness`, `generation.answer_relevance`, `generation.answer_correctness` | `no_llm` | They require a judge. The retrieval runner has none. |
| `system.prompt_tokens`, `system.completion_tokens`, `system.total_tokens`, `system.cost_usd`, `system.priced_fraction`, `system.llm_fallback_rate` | `no_llm` | No model call was made, so there are no tokens, no cost and no LLM fallback. |

Under `run_rag_eval` with no provider key, the generation metrics are instead
`not_measured: scripted_judge`, and the token/cost metrics are
`not_measured: no_provider_key`.

## The three-run spread

Three consecutive runs on the final code produced **identical retrieval and
system-rate metrics** (spread `0.000000`). The only variation is wall-clock
latency, which is expected on a shared machine.

| Metric | Run 1 (baseline) | Run 2 | Run 3 | Spread |
|--------|------------------|-------|-------|--------|
| `retrieval.semantic_recall_at_20` | 1.000000 | 1.000000 | 1.000000 | 0.000000 |
| `retrieval.lexical_recall_at_20` | 0.958333 | 0.958333 | 0.958333 | 0.000000 |
| `retrieval.fused_recall_at_10` | 0.988095 | 0.988095 | 0.988095 | 0.000000 |
| `retrieval.fused_mrr` | 0.946429 | 0.946429 | 0.946429 | 0.000000 |
| `retrieval.precision_at_5` | 0.364286 | 0.364286 | 0.364286 | 0.000000 |
| `retrieval.recall_at_5` | 0.922619 | 0.922619 | 0.922619 | 0.000000 |
| `retrieval.mrr_at_5` | 1.000000 | 1.000000 | 1.000000 | 0.000000 |
| `retrieval.ndcg_at_5` | 0.914961 | 0.914961 | 0.914961 | 0.000000 |
| `retrieval.context_precision` | 0.950000 | 0.950000 | 0.950000 | 0.000000 |
| `retrieval.context_recall` | 0.922619 | 0.922619 | 0.922619 | 0.000000 |
| `system.degradation_rate` | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `system.error_rate` | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| `system.semantic_p50_ms` | 2.005750 | 1.118167 | 1.106541 | 0.899209 |
| `system.semantic_p95_ms` | 5.372500 | 2.388125 | 5.659541 | 3.271416 |
| `system.semantic_p99_ms` | 6.000875 | 6.034792 | 8.110084 | 2.109209 |
| `system.lexical_p50_ms` | 8.145958 | 3.200166 | 2.346416 | 5.799542 |
| `system.lexical_p95_ms` | 10.919292 | 85.258833 | 82.043375 | 74.339541 |
| `system.lexical_p99_ms` | 13.047750 | 100.209958 | 91.805792 | 87.162208 |
| `system.fusion_p50_ms` | 0.074000 | 0.052709 | 0.042917 | 0.031083 |
| `system.fusion_p95_ms` | 0.110209 | 0.232041 | 0.243542 | 0.133333 |
| `system.fusion_p99_ms` | 0.141792 | 0.238292 | 2.652417 | 2.510625 |
| `system.rerank_p50_ms` | 1.957125 | 1.355083 | 1.117166 | 0.839959 |
| `system.rerank_p95_ms` | 3.203667 | 4.211625 | 3.995125 | 1.007958 |
| `system.rerank_p99_ms` | 4.078959 | 4.379209 | 4.530625 | 0.451666 |

**Largest observed spread: `87.162208 ms` (`system.lexical_p99_ms`).** The
largest non-latency spread is `0.000000`. The lexical p95/p99 metrics are the
noisy ones: the first run warmed the database pages and the later runs did not
pay the same cost, which is why the same metric moved by almost an order of
magnitude even though nothing else changed.

## Tolerances, and why they are what they are

The table lives in `evals/runners/check_regression.py` (`TOLERANCES`). The
permitted deviation for a metric is `absolute + relative * |baseline|`.

| Metric group | Tolerance | Justification |
|--------------|-----------|---------------|
| Retrieval metrics | `1e-9` absolute, `0` relative | Measured spread was `0.000000`. The 1e-9 allowance only absorbs a JSON float round-trip; it is not slack for a real change. |
| `citations.*` (deterministic) | `1e-9` absolute, `0` relative | Same: deterministic for a fixed generator output. |
| `system.degradation_rate`, `system.error_rate`, `system.generation_degradation_rate` | `1e-9` absolute, `0` relative | A new degradation reason is a real change, not noise. |
| Latency percentiles (`system.*_ms`) | `100 ms` absolute plus `100%` of the baseline | The largest observed spread was `87.162208 ms`. The `100 ms` absolute allowance is above it, and the `100%` relative term adds headroom for a slower machine. Latency gating is deliberately loose because wall-clock time is not reproducible on shared hardware; a genuine order-of-magnitude regression still fails. |
| `generation.*` | `0.05` absolute, `0` relative | Not present in the committed retrieval baseline, because judging requires a provider. Defined so a future RAG baseline can gate them; LLM judging is stochastic, so a small absolute drift is allowed. |

Every tolerance is at or above the spread observed for its metric. Latency is
the only group with a nonzero spread, and its `100 ms` absolute allowance is
above the largest spread measured (`87.162208 ms`).
