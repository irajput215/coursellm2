# The evaluation system

This directory contains the golden dataset, the retrieval corpus, the metric
implementations, the LLM-as-judge, the runners that produce report artefacts,
and the regression gate. It complements `apps/api/tests/`, which verifies the
components; this directory measures end-to-end behaviour and publishes the
result as a committed, reproducible artefact.

The governing rule is simple and absolute:

> **No number appears in a report or a README unless a run artefact produced
> it.** A metric that cannot be measured in the current environment is recorded
> in the report's `not_measured` map with a reason — never omitted, never
> estimated, never scored as zero.

## Layout

| Path | What it is |
|------|------------|
| `datasets/golden_rag.jsonl` | 32 questions with expected answers and expected source documents |
| `datasets/corpus/*.md` | 8 project-authored reference documents |
| `datasets/README.md` | how the dataset was built and what it does not cover |
| `metrics/retrieval.py` | recall@k, precision@k, MRR, nDCG@k, context precision, context recall |
| `metrics/generation.py` | faithfulness, answer relevance, answer correctness (judge-backed) |
| `metrics/citations.py` | citation precision, citation recall, hallucination rate |
| `metrics/system.py` | latency percentiles, tokens, cost and coverage, rates |
| `judges/llm_judge.py` | the `Judge` protocol, `LLMJudge`, and the deterministic `ScriptedJudge` |
| `runners/run_retrieval_eval.py` | the LLM-free half: retrieval, citation and system metrics |
| `runners/run_rag_eval.py` | the same plus generation and judging |
| `runners/check_regression.py` | the gate: compares a report to the baseline |
| `reports/baseline.json` | the committed baseline, produced by a real retrieval run |
| `reports/README.md` | how a report is produced, and what the baseline was measured on |

## How a run works

Both runners build a throwaway tenant and course, ingest the corpus through the
**production ingestion pipeline**, and run every golden question through
`hybrid_search` → `rank`. Nothing is stubbed:

* embeddings come from the real `HashingEmbedder` (`embedding_provider=hashing`),
  which is deterministic and needs no model download or network;
* reranking uses the real `LexicalReranker`, which is dependency-free and
  deterministic;
* the retrieval runner makes **no LLM call at all**.

The full RAG runner adds context assembly and generation. It uses the real
`LLMGateway` and `LLMJudge` only when a provider key is configured
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GROQ_API_KEY`).
Without a key no model is called: the generator's deterministic **extractive
fallback** produces the answer, the `ScriptedJudge` exercises the harness
mechanics, and every judge-derived metric is recorded as `not_measured` with
reason `scripted_judge`. The scripted judge's token-overlap scores are never
written into `metrics`.

## Determinism

A regression gate is only meaningful if two runs on unchanged inputs agree.
Fusion and the reranker break exact score ties by chunk id, and production
generates those ids randomly, so the harness installs a **count-based id
sequence** for the rows it creates (`deterministic_row_ids`). The production
code path is untouched — the original column defaults are restored when the run
ends — and the tie-break becomes reproducible. The observed run-to-run spread is
documented in `reports/README.md`.

## What is measured, and what is not

| Metric group | Retrieval runner | Full RAG runner, no key | Full RAG runner, with key |
|--------------|------------------|-------------------------|---------------------------|
| Retrieval (recall, precision, MRR, nDCG, context precision/recall) | measured | measured | measured |
| Latency percentiles, retrieval degradation rate, error rate | measured | measured | measured |
| Citation precision/recall/hallucination | `not_measured: no_generation` | measured on the extractive fallback | measured |
| Faithfulness, answer relevance, answer correctness | `not_measured: no_llm` | `not_measured: scripted_judge` | measured |
| Prompt/completion tokens, cost, priced fraction, LLM fallback rate | `not_measured: no_llm` | `not_measured: no_provider_key` | measured |

Citation metrics from the no-key run describe the deterministic extractive
fallback, not a generated answer. They are labelled by
`generation_mode="extractive_fallback"` and must not be read as generation
quality.

Optional RAGAS: `metrics/` is a native implementation with no RAGAS dependency.
The `ragas` extra exists but is not required; if a future adapter is added it
must be imported lazily and the report must say which engine produced a number.

## Running

```bash
make eval-retrieval   # writes evals/reports/retrieval.json; no LLM required
make eval             # writes evals/reports/latest.json; extraction or real LLM
make eval-gate        # compares latest.json to baseline.json; non-zero on breach
```

Or directly, from the repository root:

```bash
.venv/bin/python -m evals.runners.run_retrieval_eval --output /tmp/retrieval.json
.venv/bin/python -m evals.runners.check_regression \
    --baseline evals/reports/baseline.json --current /tmp/retrieval.json
```

Both runners honour `EVAL_DATASET_PATH`, `EVAL_SAMPLE_LIMIT` and
`DATABASE_URL` (through `Settings`), and exit non-zero on a harness error as
opposed to a regression.

## The regression gate

`check_regression.py` holds the **single tolerance table**. For each metric in
the baseline it compares the current value and fails when it moves beyond
`absolute + relative * |baseline|`. Three properties matter:

* a `not_measured` metric is skipped and printed with its reason — it is neither
  a pass nor a failure;
* a metric present in the baseline but absent from the current report fails;
* a report whose `retrieval_config_version` or dataset hash differs is refused
  (exit code 2) unless `--allow-config-change` is passed, because comparing
  across a configuration change is exactly the stale-cache-versus-real-change
  confusion ADR-0010 exists to prevent.

Exit codes: `0` clean, `1` breach or missing metric, `2` refused comparison.

## Tooling

`evals/` is linted and type-checked to the same standard as `apps/api`. Ruff
discovers configuration per file, and `evals/` sits outside the `apps/api`
project, so the repository root carries a `pyproject.toml` with the mirrored
ruff and mypy sections. `make lint` and `make typecheck` cover both trees.
