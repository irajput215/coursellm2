# coursellm (API)

The CourseLLM backend: an async FastAPI service providing grounded tutoring over a
student's own course material.

See the [repository README](../../README.md) for the product overview and
[`docs/architecture/`](../../docs/architecture/) for the design.

---

## Layer map

| Package | Responsibility |
|---------|----------------|
| `coursellm.core` | Configuration, logging with redaction, error hierarchy, telemetry |
| `coursellm.db` | SQLAlchemy models, async session management, tenancy context, RLS helpers |
| `coursellm.repositories` | The only sanctioned access path to tenant-scoped data; every repository requires a tenant scope |
| `coursellm.llm` | LiteLLM gateway: task-based routing, retries, fallbacks, cost accounting |
| `coursellm.rag` | Ingestion, hybrid retrieval, fusion, reranking, context assembly, generation |
| `coursellm.graph` | Knowledge graph extraction and traversal |
| `coursellm.agents` | LangGraph state machine and its nodes |
| `coursellm.tools` | Typed, permission-checked tools the agents may call |
| `coursellm.security` | Injection detection, content delimitation, output validation |
| `coursellm.services` | Use-case orchestration shared by routers and agents |
| `coursellm.api` | FastAPI application factory and domain routers |

Dependencies point inward. Routers hold no business logic; domain code does not
import FastAPI.

---

## Running

```bash
make install        # from the repository root
make db-create
make migrate
make api            # http://localhost:8000/docs
```

Tests:

```bash
make test             # fast unit tests, no external services
make test-integration # requires PostgreSQL (TEST_DATABASE_URL)
make test-security    # adversarial regression tests
make test-all         # everything, with coverage
```

---

## Conventions

- **Async throughout.** Database, cache and provider calls are awaited. Blocking
  work (model inference, file parsing) is pushed off the event loop.
- **No provider SDKs outside `coursellm.llm`.** All model access goes through the
  gateway so routing, fallback and cost accounting cannot be bypassed.
- **No secrets, prompts or document text in logs or traces** unless explicitly
  opted in. Redaction is applied by a logging processor, not by convention.
- **Tenant identity is never an LLM-supplied argument.** It comes from the
  authenticated request context.
- **Two database roles.** The application connects as a role with `NOSUPERUSER
  NOBYPASSRLS`; the schema owner runs migrations. PostgreSQL ignores every
  Row-Level Security policy for a superuser, so a single-role setup makes tenant
  isolation look enforced when it is not. `make db-inspect` reports which role
  you are connected as.
- **Every external dependency has a declared failure path** and a test for it.
