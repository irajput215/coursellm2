# ADR-0010: Prompt versioning and retrieval configuration hashing

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0005, ADR-0006, ADR-0007

## Context

The prototype embedded prompts in code: `services/llm.py` hard-codes a placeholder
system prompt (`"you are an astronaut, ..."`), and `rag/generation/generator.py`
builds a multi-rule system prompt as a string literal inside
`AnswerGenerator.__init__`. There is no prompt identifier, no version, and no record
of which prompt produced an answer. Retrieval configuration (top-k, fusion weights,
model names) is similarly spread across constants and environment variables.

A quality change is therefore impossible to attribute. When answer quality moves, the
cause could be a prompt edit; a retrieval configuration change (top-k, `RRF_K`, BM25
parameters, reranker settings, context budget, embedding model); a corpus change; a
model or provider change (ADR-0007); or a **stale cache** — a Redis entry keyed only
by query text, written under one configuration and served after another was deployed.

The stale cache is the most insidious: the system appears to have changed behaviour
when it has replayed old behaviour. A prompt rollback can "fail to fix" a regression
the prompt never caused, and LangSmith comparisons become meaningless because two
runs labelled with the same prompt were executed under different retrieval
configurations. The architecture already commits to the mechanism in
`docs/architecture/rag.md` §10: retrieval configuration is hashed into
`RETRIEVAL_CONFIG_VERSION`, which forms part of the cache key and is recorded on every
evaluation run.

## Decision

Treat prompts and retrieval configuration as **versioned, hashed inputs**, and record
both on every evaluation run and in every relevant cache key.

### Prompts

- Prompts live as files under `prompts/` (for example `prompts/tutor/v3.md`), not as
  string literals in code.
- Each file carries front matter: prompt id, version, task, input variables, and the
  expected output schema where structured output is required.
- Published versions are immutable; an edit creates a new version, the previous
  version is retained, and configuration selects one.
- Files are parsed and validated at startup: unknown placeholders, missing inputs, and
  schema mismatches fail fast rather than at request time.
- Every model call records the prompt id and a content hash of the rendered template,
  so an answer traces to the exact prompt that produced it.

### Retrieval configuration

`RETRIEVAL_CONFIG_VERSION` is a digest over the canonical JSON of the
retrieval-affecting settings:

```text
sha256(canonical_json({
  embedding_model, embedding_dim,
  retrieval_top_k_per_retriever, rrf_k,
  bm25_k1, bm25_b,
  rerank_enabled, reranker_model, rerank_top_k, rerank_timeout_ms,
  context_token_budget, hnsw_ef_search,
  hybrid_lexical_weight_enabled and its weights,
  filter defaults
}))
```

Canonicalisation fixes key order and number formatting, so the same configuration
always yields the same digest; secrets and non-retrieval settings are excluded.

- The digest is part of the retrieval and embedding cache key alongside tenant and
  query: `key = H(tenant_id, query, RETRIEVAL_CONFIG_VERSION)`, so a configuration
  change produces a new key and old results cannot be served under the new config.
- The digest is recorded on every `EVAL_RUN` with the prompt id and hash, model and
  provider, dataset version, and code commit.
- The canonical configuration snapshot is stored alongside the digest, so a digest
  can always be mapped back to the configuration that produced it.

### How this prevents misattribution

A quality change after a deploy is diagnosable by comparing configuration tuples
rather than guessing. Because the cache key contains the retrieval hash, a stale hit
under an old configuration is impossible; because the run records the prompt hash, a
prompt change is separable from a retrieval change; because both are recorded with
the dataset version and commit, a corpus change is separable from both. In LangSmith,
experiment comparison groups runs by these fields and refuses to compare runs whose
prompt or configuration hashes differ without flagging it.

## Consequences

### Positive

- A quality change is attributable to a specific cause: prompt, retrieval
  configuration, corpus, model, or code, and evaluation runs are reproducible.
- A/B experiments are comparable, because runs differing in configuration are
  labelled as such rather than silently pooled.
- Cache correctness improves: a configuration change cannot serve results produced
  under the previous configuration.
- Prompt changes become reviewable diffs with a rollback path that need not be a code
  deploy if the loader supports version selection, and the mechanism is cheap: hashing
  happens at startup and costs a few hundred bytes of metadata per run and per trace.

### Negative

- Prompt files are indirection: developers must publish a version rather than edit a
  string, and the variable contract between prompt and filling code must be enforced
  or it drifts.
- A digest is opaque; without the stored canonical snapshot a hash cannot be
  interpreted, so the snapshot is mandatory rather than optional.
- Canonicalisation is a correctness requirement: inconsistent key ordering, float
  formatting, or normalisation makes the same configuration hash differently and
  defeats the mechanism.
- The digest covers only the listed settings. Adding a new retrieval-affecting knob
  without adding it to the hash silently under-versions the configuration, so the list
  needs a guard.
- Cache hit rate drops immediately after any configuration change because every key is
  new; this is intended cold-cache behaviour with a warm-up cost.
- Prompt files create a second place (alongside code) where behaviour can live.

### Neutral

- Prompts remain plain text; no prompt-management SaaS is required, and files are the
  source of truth. A hosted registry could be added later without changing the identity
  model.
- The configuration digest is an identity, not a semantic version; humans still assign
  prompt versions.

## Alternatives considered

- **Prompts inline in code (prototype).** No version identity, deploy-coupled changes,
  and no way to tell which prompt produced an answer. Rejected.
- **Prompts in mutable database rows.** Runtime-editable, but hard to review, easy to
  change untraceably, and reproducibility then depends on row history that may not be
  retained. Rejected as the source of truth.
- **Cache keys built from the query alone.** Simplest, and rejected: it is exactly what
  produces stale results after a configuration change and makes evaluation attribution
  impossible.
- **Relying on the commit SHA alone.** Couples configuration identity to code deploys,
  so a configuration change made by environment variable — a common operational action
  — would be invisible. Rejected as insufficient.

## How this is verified

- A test changes each retrieval-affecting setting in turn and asserts
  `RETRIEVAL_CONFIG_VERSION` changes; a second reorders the same configuration and
  asserts the digest is unchanged.
- A guard test enumerates the settings in the configuration model and fails if one is
  not covered by the hash, so a new knob cannot be added silently.
- A cache test asserts two configuration versions never share a key and that a response
  served after a configuration change was not produced under the previous one.
- `EVAL_RUN` rows are asserted to carry a non-null prompt hash and configuration hash;
  `evals/reports/` renders the canonical configuration snapshot alongside the digest so
  any run can be reconstructed.
- Startup validation asserts every prompt file parses, its declared placeholders match
  the caller's inputs, and its expected output schema validates.
