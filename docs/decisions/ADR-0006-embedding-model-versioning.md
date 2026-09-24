# ADR-0006: Embedding model versioning in a separate chunk_embeddings table

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0002, ADR-0005, ADR-0010

## Context

The prototype stored one vector as a column on the chunk row
(`coursellm/models/chunk.py`):

```python
embedding: Any = Field(sa_column=Column(Vector(384)))
# Vector embedding using pgvector. BAAI/bge-small-en-v1.5 -> 384 dims
```

The model was recorded only in a comment, the schema encoded its dimensionality,
and a chunk could hold exactly one vector. Changing model meant a column rewrite
(`9fc059ca10c7_add_chunk_index_and_update_vector_size.py` did this) with no ability
to compare generations, no rollback, and a window in which queries were compared
against a mixture of old-space and new-space vectors. That mixture does not error;
it silently returns wrong neighbours.

Embeddings from different models are **not comparable even at the same
dimensionality**: tokeniser, training objective, pooling, and normalisation differ,
so the vector space is part of the data's identity, not an implementation detail. A
cosine distance between a query embedded by model *B* and a chunk embedded by model
*A* is meaningless, and the ANN index will return it anyway.

## Decision

Vectors live in a dedicated **`chunk_embeddings`** table keyed by the vector space:

```text
chunk_embeddings(
  id, chunk_id -> chunks(id), tenant_id,
  embedding_model text, dim int, embedding vector, created_at,
  UNIQUE (chunk_id, embedding_model, dim)
)
```

- `chunks` is model-agnostic and holds no vector column.
- Every retrieval query filters `embedding_model = :model` (and validates `dim`), so
  a query embedded by one model can never be compared against another's vectors.
- HNSW indexes are **partial**, one per active model, because one HNSW graph cannot
  mix vector spaces: `... USING hnsw (embedding vector_cosine_ops) WHERE
  embedding_model = '<model>'`.
- `dim` is part of the unique key. Because pgvector's `vector(n)` typmod fixes one
  dimension per column, a different dimensionality lands in a sibling table or
  partition rather than the same column — explicit, not accidental.
- `EMBEDDING_MODEL` and `EMBEDDING_DIM` are configuration validated against stored
  rows at startup.

### Zero-downtime re-index strategy

```mermaid
sequenceDiagram
    autonumber
    participant W as Write path
    participant BF as Backfill
    participant DB as chunk_embeddings
    Note over W: new model configured INACTIVE; A still active
    W->>DB: dual-write generations A + B
    BF->>DB: backfill existing chunks with B (batched, idempotent upsert)
    Note over DB: CREATE INDEX CONCURRENTLY (partial, model B)
    Note over DB: evals/ A/B recall and MRR -> promotion decision
    W->>DB: flip EMBEDDING_MODEL = B
    Note over DB: A retained for rollback window, then deleted + index dropped
```

1. The new model is configured inactive; the write path still embeds with the
   active model.
2. Dual-write is enabled, so new and updated chunks get both generations. Backfill
   embeds the existing corpus with the new model in batches, upserting on the unique
   key so it is idempotent and resumable, rate-limited to respect provider limits.
3. The partial HNSW index for the new model is built with `CREATE INDEX
   CONCURRENTLY` (outside a transaction) so writes are not blocked.
4. `evals/` runs both generations on the golden dataset; promotion requires the new
   model to meet the retrieval thresholds.
5. `EMBEDDING_MODEL` (and `EMBEDDING_DIM`) are flipped and the service deployed;
   retrieval filters the new model, and cache keys include the model, so no
   old-space result can be served.
6. Old-generation rows and their index are **retained** for a rollback window;
   rollback is a configuration flip with no re-embedding. After the window, old rows
   are deleted and the old partial index dropped in a follow-up migration.

## Consequences

### Positive

- A model change is additive, not destructive: generations coexist and can be
  benchmarked on identical chunks.
- Rollback is a config change with no re-embedding cost while old rows remain.
- A query can never be compared against a vector from a different model, because the
  model is an explicit predicate and part of the key.
- The vector space is observable: rows carry the model and dimension that produced
  them, each run records the model it used (ADR-0010), and the chunk table stays
  stable so unrelated migrations do not touch vectors.

### Negative

- **Storage roughly doubles during a migration.** A `vector(1536)` value is
  `1536 × 4 = 6144` bytes of raw float32 — about 6 KiB per chunk per model. One
  million chunks is roughly 6 GB of raw vectors per generation, plus the HNSW index,
  which stores its own copy of each vector and the graph links; a workable planning
  estimate is two to three times the raw column size per generation, with the exact
  factor measured by the migration job rather than assumed.
- Backfill is a real cost: re-embedding the corpus consumes provider tokens and time
  and must be rate-limited and resumable.
- Queries gain a predicate, and with partial indexes the planner must select the
  right index; a missing or stale partial index silently falls back to a sequential
  scan.
- More indexes mean more build time, more WAL, and longer maintenance windows, and
  the cleanup step is easy to forget: a forgotten generation accumulates cost, so
  the rollback window must be enforced by a scheduled job.
- A dimensionality change cannot reuse the same column, so it needs a sibling table
  or partition and matching migration logic.

### Neutral

- Only one generation is active for retrieval at a time; dual-write is a migration
  mechanism, not a permanent mode.
- The embedding provider call is cached, so repeated backfill attempts do not
  necessarily pay twice.

## Alternatives considered

- **A vector column on `chunks` (prototype).** Simplest to query; rejected for one
  space per chunk, destructive model changes, no rollback, implicit model identity.
- **One table per embedding model.** Clean isolation, but schema churn on every model
  change and cross-model queries become unions. Rejected.
- **Overwrite vectors in place.** No extra storage, but no rollback, no A/B
  comparison, and a corruption window during migration. Rejected.
- **Embed on read with the current model.** Always current, never stale, but
  retrieval latency and cost then depend on the provider for every query. Rejected.
- **A mapping table holding only the model id.** Does not solve multi-generation or
  rollback.

## How this is verified

- `apps/api/tests/integration/test_embedding_versioning.py` asserts retrieval returns only rows for the
  configured model and dimension, and that a query vector from model B cannot match
  a stored vector from model A.
- A migration integration test loads a synthetic two-model, two-dimension corpus,
  runs dual-write and backfill, and asserts the unique key prevents duplicates and
  the backfill is idempotent under retry.
- `evals/` A/B comparison of the generations gates promotion; deltas are published
  in `evals/reports/` and no figure is quoted here.
- A rollback test restarts with the previous model configured and asserts retrieval
  succeeds against retained old rows without re-embedding; startup validation fails
  fast if `EMBEDDING_DIM` does not match the dimension recorded for the configured
  model, or if no rows exist for it.
