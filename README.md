# CourseLLM

**An agentic RAG tutoring platform.** A student uploads their own course
material, states a goal, and gets grounded tutoring, a prerequisite-aware
learning roadmap, real resource recommendations, quizzes, and progress-adaptive
planning — backed by hybrid retrieval, a knowledge graph, and evaluated AI
quality.

> **Status: under active rebuild.** This repository is being rebuilt from a
> prototype into a production-oriented system, one reviewable pull request at a
> time. The prototype has been audited and removed; its replacement is being
> built incrementally.

---

## Where to start

| Document | What it covers |
|----------|----------------|
| [`docs/FINAL_AUDIT.md`](docs/FINAL_AUDIT.md) | Repository-wide audit: requirement traceability, claims checked against code, remaining gaps |
| [`docs/PROJECT_AUDIT.md`](docs/PROJECT_AUDIT.md) | Audit of the prototype: what existed, what was wrong, and the migration plan |
| [`docs/architecture/system.md`](docs/architecture/system.md) | System architecture, component justification, failure model |
| [`docs/architecture/rag.md`](docs/architecture/rag.md) | Hybrid retrieval, BM25, RRF, reranking, citations |
| [`docs/decisions/`](docs/decisions/) | Architecture decision records |
| [`evals/README.md`](evals/README.md) | How AI quality is measured |

A full README with measured results and diagrams is produced at the end of the
rebuild. Nothing is published here that cannot be regenerated from this
repository.

### Measured retrieval quality (scoped)

`evals/reports/baseline.json` is a **retrieval-only** run: it uses the
deterministic `hashing` embedder (`hashing-v1`, not a semantic model) and the
lexical-fallback reranker over a small corpus (8 documents, 14 chunks) and 32
golden questions (28 answerable, 4 unanswerable). Under that scope,
context precision is 0.95, context recall 0.9226, fused MRR 0.9464 and fused
recall@10 0.9881. Generation faithfulness, citation precision/recall and
token/cost metrics are recorded as `not_measured` because CI runs no LLM
provider. Reproduce with `make eval-retrieval && make eval-gate`.

No screenshots are embedded yet: the UI has not been captured from a running
app in this environment, and placeholders would overstate what is shown.

---

## Quick start

```bash
make install        # core + dev dependencies
make db-setup       # databases, schema, and the restricted application role
make api            # http://localhost:8000/docs
```

`make db-setup` provisions two database roles: the schema owner that runs
migrations, and `coursellm_app`, which runs the application and **cannot bypass
Row-Level Security**. That split is not ceremony — PostgreSQL ignores every RLS
policy for a superuser, so developing as one hides the failure until production.
`make db-inspect` reports which role you are connected as and whether tenant
isolation is actually enforced.

```bash
make test           # fast unit tests
make verify         # lint + typecheck + secret scan + full test suite
make help           # every available target
```

---

## Repository layout

```text
apps/api/         FastAPI service: retrieval, agents, tools, security, evaluation hooks
apps/web/         React + TypeScript client
evals/            Golden dataset, runners, metrics, committed report artefacts
prompts/          Versioned prompt files
infra/terraform/  AWS infrastructure as code
docs/             Audit, architecture, decision records
scripts/          Developer and CI helpers
```

An MCP server exposing selected tools to external AI clients is planned but not
present; it is not listed above until it exists.

---

## Principles

These are enforced by tests, not just stated:

1. **Retrieval before generation.** No course answer without retrieved evidence.
2. **Agents decide, tools execute.** Deterministic logic never lives in a prompt.
3. **Tenancy is part of the query, not a filter applied afterwards.**
4. **Every external dependency has a declared failure path.**
5. **AI quality is a testable engineering property**, enforced against a versioned dataset.
6. **No technology without a load-bearing job.**
7. **Untrusted text is never trusted text.**

---

## Licence

MIT
