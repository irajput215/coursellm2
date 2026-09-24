# ADR-0002: PostgreSQL with pgvector as the single datastore

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0003, ADR-0006, ADR-0009

## Context

The platform has four access patterns over the same domain objects: relational
reads and writes, approximate nearest-neighbour search, lexical ranking, and
prerequisite traversal.

The prototype used Postgres with pgvector but modelled the vector as a column on
the chunk row: `coursellm/models/chunk.py` declares
`embedding: Any = Field(sa_column=Column(Vector(384)))`, with the dimension and
model baked into the table and only a comment
(`BAAI/bge-small-en-v1.5 -> 384 dims`) recording the model. A migration
(`9fc059ca10c7_add_chunk_index_and_update_vector_size.py`) shows a vector-size
change was a schema migration, and a chunk could hold only one vector, so a model
change was destructive and irreversible (ADR-0006).

The obvious alternative is a split architecture: relational data in Postgres,
vectors in a dedicated store (Pinecone, Weaviate, Qdrant, Milvus). That buys
specialised ANN performance but adds a second system of record, a dual-write or
change-data-capture path between metadata and vectors, cross-store consistency
problems, two backup and disaster-recovery procedures, two observability surfaces,
and a tenancy model enforced correctly in two places.

## Decision

Use **one PostgreSQL 16+ instance with pgvector** for all four patterns.

- **Vectors** live in `chunk_embeddings` (ADR-0006) with an HNSW index
  (`m = 16`, `ef_construction = 64`, `vector_cosine_ops`); recall and latency are
  tuned per session with `hnsw.ef_search`.
- **Relational data** uses tenant-scoped tables whose composite indexes lead with
  `tenant_id`.
- **Lexical search** uses explicit BM25 tables (`chunk_terms`,
  `tenant_lexical_stats`, `tenant_corpus_stats`) with a GIN index on a generated
  `tsvector` for candidate generation (ADR-0003); `ts_rank` is not used for
  ranking.
- **The knowledge graph** is a property-graph-shaped pair of tables traversed by
  recursive CTEs (ADR-0004).
- **Tenancy** is enforced in the database with RLS (ADR-0009), so one policy
  protects all four patterns.

Chunk metadata and its embeddings are written in one transaction. The ANN query
joins `chunk_embeddings` to `chunks` and applies `tenant_id`, `embedding_model`,
`course_id`, `document_id`, and `topic` as scalar predicates inside the scan,
using tenant-first B-tree indexes plus `hnsw.iterative_scan = strict_order` for
selective filters. Filtering *after* the ANN scan is explicitly rejected: if a
tenant owns a small share of the corpus, a post-filtered `LIMIT k` returns far
fewer than `k` rows — worst case zero — because the global neighbours belong to
other tenants. Recall collapses while latency still looks healthy.

## Consequences

### Positive

- Chunk metadata and its vector are updated atomically: no window with a chunk
  lacking an embedding or an embedding pointing at a deleted chunk.
- One backup, PITR, failover, upgrade, and observability story; one connection
  pool and one planner to reason about.
- Filters, ordering, and tenancy use one language and one RLS policy, so vector
  search cannot be the path that forgets `tenant_id`.
- No change-data-capture pipeline, no dual-write inconsistency, no second schema
  to migrate; managed Postgres is available on every major cloud at predictable
  cost.

### Negative

- HNSW index builds are memory- and time-intensive, and the index needs
  maintenance: update/delete churn is reclaimed by `VACUUM`/`REINDEX`, and index
  parameters cannot change without a rebuild.
- pgvector is single-node scale-up. At very large vector counts the working set no
  longer fits in memory and latency degrades where a sharded index would not.
- Vector data and HNSW structures live in the primary, enlarging backups and WAL.
- There is no native multi-vector (late-interaction) or learned-sparse index, so
  ColBERT-style or SPLADE-style retrieval needs a different engine.
- `vector(n)` fixes one dimension per column, so a dimensionality change needs a
  new table or partition (a known cost recorded in ADR-0006).

### Neutral

- Vectors live only in `chunk_embeddings`; `chunks` is model-agnostic.
- HNSW is chosen over IVFFlat because IVFFlat needs representative training data up
  front and its lists degrade as the corpus grows, whereas HNSW needs no training
  and tolerates incremental inserts.

### When a dedicated vector store becomes justified

- hundreds of millions of vectors per collection, beyond one primary's indexing and
  residency limits;
- filtered ANN at sustained high QPS where Postgres cannot hold the latency target
  after tuning `ef_search`, composite indexing, and partitioning;
- a requirement for multi-vector or late-interaction retrieval, or for sparse-dense
  hybrid indexes pgvector does not provide;
- a need for GPU-accelerated or distributed ANN.

The migration path is cheap because `chunk_embeddings` is isolated behind a
repository interface: keep Postgres as the source of truth for chunks and metadata,
move only vector search, dual-write via an outbox until the backfill is verified,
and switch back-ends behind the same interface.

## Alternatives considered

- **A dedicated vector database (Pinecone, Weaviate, Qdrant, Milvus).** Fast and
  capable, but a second system of record that cannot join a Postgres transaction;
  it adds operations, a synchronisation path, and a second tenancy mechanism for a
  workload that fits in one Postgres. Rejected for now.
- **Postgres plus in-process FAISS.** Fast ANN, but the index is per-process, not
  durable, not transactionally related to the rows, and must be rebuilt or
  distributed by hand. Rejected.
- **SQLite with a vector extension.** No production HA, weak concurrency, no RLS.
  Rejected.
- **Elasticsearch/OpenSearch for lexical and vectors.** Strong lexical scoring, but
  it replaces a datastore already needed and adds another cluster and a sync path.
  Rejected.

## How this is verified

- Retrieval SQL tests assert ANN queries apply tenant and model predicates in the
  scan, and `EXPLAIN` checks assert the HNSW and composite indexes are used.
- `tests/test_tenant_isolation.py` runs an adversarial corpus in which the
  overwhelming majority of chunks belong to a decoy tenant and asserts `LIMIT k`
  returns `k` rows for the requesting tenant.
- Recall@20, Recall@10, and MRR per access pattern are produced by `evals/` on the
  golden dataset; per-layer latency is captured by the spans in
  `docs/architecture/system.md` §5. No figure is asserted here without a run
  artefact in `evals/reports/`.
- A periodic restore drill verifies one backup restores metadata, vectors, lexical
  statistics, and graph edges to a consistent point in time — the property a split
  architecture would not give for free.
