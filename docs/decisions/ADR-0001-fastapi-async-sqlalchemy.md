# ADR-0001: FastAPI with async SQLAlchemy 2.0 and asyncpg

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0002, ADR-0008

## Context

The prototype used FastAPI (`coursellm/main.py`) but was asynchronous in name
only. Routes such as `api/routes/ask.py` were `async def` while the layer beneath
them was synchronous: `database.py` builds a `create_engine(...)` engine
(`pool_size=10`, `max_overflow=20`) and yields a `sqlmodel.Session`; the
retrievers call `self.session.exec(...)`/`self.session.execute(...)`; the LLM path
uses a synchronous `openai.OpenAI` client and a `pydantic_ai` agent. Every awaited
call therefore blocked the event loop or occupied a worker thread.

The request path is overwhelmingly I/O-bound: one embedding HTTP call, one or
more Postgres round trips (ANN and BM25), optionally a cross-encoder forward
pass, and one or more LLM calls. Almost all wall-clock time is spent waiting, so a
synchronous stack makes concurrency a function of thread-pool size — the
prototype's own `pool_size=10` capped in-flight work — rather than of available
I/O capacity.

Three further constraints shaped the choice. Request/response validation, tool
schemas, and LLM structured outputs all describe the same domain objects, and the
prototype mixed Pydantic, SQLModel, and SQLAlchemy models across `schemas/`,
`models/`, and `api/routes/`. The React client is generated from the HTTP
contract (`docs/architecture/system.md` §4.3), so the API must publish an accurate
OpenAPI schema. And the prototype had Alembic configured yet also called
`SQLModel.metadata.create_all(engine)` at import, so the schema was not reliably
migration-driven.

## Decision

Keep **FastAPI**, and rebuild persistence and I/O on **async SQLAlchemy 2.0 +
asyncpg**:

1. `AsyncEngine` over `postgresql+asyncpg://`; `AsyncSession` per request from
   `async_sessionmaker`; explicit transaction boundaries
   (`async with session.begin()`); no session outlives a request.
2. No implicit lazy loading: relationships are fetched with
   `selectinload`/`joinedload` or explicit joins, because lazy I/O raises
   `MissingGreenlet` under asyncio. `expire_on_commit=False` so objects remain
   usable after commit.
3. Pydantic v2 is the single schema layer, reused for HTTP bodies, tool schemas,
   and LLM structured outputs, so validation rules cannot drift.
4. FastAPI's OpenAPI document generates a typed TypeScript client; a breaking
   contract change is a client compile error.
5. Schema changes go through Alembic only; startup never emits DDL.
6. CPU-bound work (reranking, local embedding) runs off the event loop in a
   bounded `run_in_threadpool` or a worker, and the boundary is explicit.

## Consequences

### Positive

- Concurrency is governed by Postgres and the providers, not by a fixed thread
  pool; a slow provider suspends a coroutine instead of occupying a worker.
- One schema layer removes the class of bugs where the HTTP model and the LLM
  output model disagree, and makes structured outputs a typed contract.
- The generated client is checked in CI, so OpenAPI drift is visible.
- Alembic gives a reviewable, ordered migration history reproducible across
  environments.

### Negative

- Async is a discipline, not a flag. One synchronous call in a request path
  (`requests`, `psycopg2`, a blocking read, a CPU-heavy function) silently
  serialises the process and must be policed in review and by test.
- Greenlet errors from accidental lazy loading are opaque, and async tracebacks
  are longer than synchronous ones.
- asyncpg is stricter than psycopg2 about type adaptation, so some call sites
  need explicit serialisation the prototype did not.
- Synchronous libraries (some evaluation and ingestion tooling) need either a
  second driver or a thread-pool hop; each is a place async correctness can be
  lost.
- Debugging is harder: breakpoints in coroutines, pool-acquisition stalls, and
  cancelled tasks all need more instrumentation.

### Neutral

- FastAPI was not the contested part; the prototype's choice is retained and only
  what sits beneath it changes.
- Async does not reduce single-request latency; it raises the concurrency
  ceiling. Per-request latency is addressed by caching, indexing, and streaming.

## Alternatives considered

- **Keep synchronous SQLModel sessions (prototype).** Simplest, but it ties
  concurrency to the thread pool and contradicts the existing `async def` routes.
  Rejected.
- **Flask with WSGI.** Mature, but a WSGI worker handles one request at a time, so
  concurrency is again thread count, with no native async or SSE. Rejected.
- **Django.** Strong ORM, admin, and auth, but the async ORM and its third-party
  ecosystem are less mature, and the framework's main advantages are not product
  requirements here. Rejected.
- **Raw asyncpg or a lighter async ORM.** Fastest and least magical, but it
  discards mapping, migration integration, and existing SQLAlchemy knowledge, and
  enlarges the repository layer. Not chosen.

## How this is verified

- A CI check that the configured database URL uses an async driver and that no
  request-path module imports a synchronous driver or sync `Session`.
- A concurrency smoke test issuing overlapping requests against a fake slow LLM,
  asserting the event loop is not blocked beyond a bound.
- An OpenAPI diff gate: the generated TypeScript client must build and typecheck.
- A migration check asserting the test database is built by Alembic and that
  startup emits no DDL.
- Per-layer latency is captured by the spans in `docs/architecture/system.md` §5;
  measured throughput under load is published by the load-test job, and no figure
  is quoted here.
