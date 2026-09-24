# Testing

> The suite is organised by *what a test is allowed to depend on*, not by which
> module it touches. That is the whole design: a unit test that quietly needs a
> database is slower than an integration test and less trustworthy than either.
> This document is the contract a new test has to satisfy, and the place to look
> when a test fails at 02:00.

Related: [`apps/api/tests/COVERAGE.md`](../apps/api/tests/COVERAGE.md) for the
measured coverage, the per-package breakdown and the named gaps;
[`docs/architecture/system.md`](architecture/system.md) §9 for which quality
attribute is demonstrated where; [`docs/architecture/security.md`](architecture/security.md)
§12 for the security test matrix.

---

## 1. The four tiers

Tests are registered against exactly one marker. `--strict-markers` is on, so an
unregistered marker is an error rather than a silently-uncollected test.

| Marker | Directory | Dependencies | What belongs here | Runtime |
|--------|-----------|--------------|-------------------|---------|
| `unit` | `tests/unit/` | None. No network, no database, no Redis, no model. | Pure functions, state machines, parsers, hashing, redaction, config validation, ASGI middleware with an in-process `httpx.ASGITransport`. | ~8 s for the whole tier |
| `security` | `tests/security/` | None, except the adversarial upload fixtures which run in-process against stubbed repositories. | Adversarial regression tests: injection classes, output redaction, tenancy boundary structure, rate-limit semantics, hardening assertions from `security.md`. A security test asserts a *control*, never a happy path. | ~3 s |
| `integration` | `tests/integration/` | A real PostgreSQL with `pgvector`, connectable as `coursellm_app` (see §3). Redis is not required; the limiter fails open. | Anything whose correctness is a property of the database or of several layers together: RLS, transaction boundaries, migrations, the ingestion pipeline, full HTTP flows, concurrency. | ~2–5 min |
| `eval` | none today | The golden dataset; the retrieval half needs no provider. | Reserved for a harness or metric test too slow for the default selection. | slow |

**Nothing carries the `eval` marker today.** The metric arithmetic and the
regression gate's tolerance logic are `unit` (`tests/unit/test_eval_metrics_*.py`,
`test_eval_regression_gate.py`), and the harness round trip is `integration`
(`tests/integration/test_eval_harness_end_to_end.py`). The corpus-level quality
gate is the `make eval-gate` command, which compares reports rather than running
pytest. The marker is kept registered so a future slow harness test has a home.

Default selection: `make test` runs `unit` only. `make verify` runs everything
except the corpus-level eval command, then runs the eval gate. The gate is
separate because it is a quality check over a dataset, not a correctness check
over code.

---

## 2. Normal CI mocks every LLM call. This is not negotiable.

**A test that needs a provider is not a test, it is an incident.** CI has no
provider key, by design, and a test that reached a provider would be
non-deterministic, cost money per run, and fail for reasons unrelated to the
code change under review.

The rule for `unit` and `security` is absolute: no test in those tiers may open
a provider connection. `LLM_ENABLED=false` resolves to
`coursellm.llm.gateway.EchoGateway`, which produces a deterministic answer with
no network call, and the test settings in `tests/conftest.py` set it
explicitly.

`integration` tests may use a *scripted* gateway, and the convention is to
subclass the real gateway and override only the provider call:

```python
class ScriptedGateway(LiteLLMGateway):
    async def _invoke(self, model, request, *, attempt, provider, used_fallback, repair=None):
        return _Completion(text=..., prompt_tokens=10, ...)
```

This matters. `tests/integration/test_chat_flow.py` overrides `_invoke` rather
than replacing the gateway, so routing, retries, response normalisation,
structured-output validation and **`llm_usage` persistence** are the production
code paths. A test that mocks `LLMGateway.complete` proves the mock works; a
test that overrides `_invoke` proves the accounting works.

Model weights are never downloaded. `EMBEDDING_PROVIDER=hashing` is the
deterministic, dependency-free embedder used by CI and the unit suite. It does
not carry meaning — it exists so retrieval correctness is testable without a
model. Reranking falls back to the deterministic lexical reranker.

---

## 3. Integration tests run as `coursellm_app`, not as the owner

**This is the single most important convention in the suite.**

PostgreSQL ignores every Row-Level Security policy for a superuser or any role
with `BYPASSRLS`, silently and without warning. A local PostgreSQL install
usually makes the developer a superuser. A test that connected as that role and
asserted "tenant B cannot see tenant A's rows" would pass — because the query
filtered correctly *in application code*, or because the table was empty — while
proving nothing about the database backstop that the architecture rests on. It
would pass for the wrong reason, which is worse than failing.

So the integration fixtures keep two engines deliberately separate
(`tests/integration/conftest.py`):

* **`pg_settings`** builds the application engine from `TEST_DATABASE_URL`,
  which must name `coursellm_app`: `NOSUPERUSER NOBYPASSRLS`, and therefore
  subject to every policy. All request paths use it.
* **`owner_engine`** connects as the schema owner and is used *only* for
  direct-SQL setup and for assertions that must see all rows regardless of RLS.
  Every `INSERT` a fixture performs goes through it, because the app role
  (correctly) cannot create another tenant's rows.

`tests/integration/test_tenant_isolation.py` asserts the property directly: the
app role reports RLS as enforced and the owner role reports it as not enforced.
If the first assertion ever fails, every other isolation test in the suite is
meaningless and the run should be treated as a failure regardless of the rest.

Provisioning:

```bash
make db-setup        # creates both databases, both roles, runs migrations,
                     # and applies scripts/bootstrap_db.sql
make db-inspect      # must print "ENFORCED by Row-Level Security"
TEST_DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test"
```

`make db-inspect` printing anything else means the integration tier is testing
the wrong role.

### Ambient environment

A local, gitignored `.env` exists in developer workspaces and is *not* present
in CI. It must never change the outcome of a test. The known trap is that
importing `coursellm.api.app` transitively imports `litellm`, which calls
`load_dotenv()` at import time and exports the developer's `.env` into
`os.environ`. A test that reads settings without disabling the env file, or that
relies on a default being unset, will then pass in CI and fail locally (or the
reverse). The rule: **tests construct `Settings(_env_file=None, ...)`
explicitly.** A test that passes only in CI is testing the absence of a file,
not the code.

---

## 4. What is deliberately **not** tested, and why

* **Real cross-encoder inference in CI.** `rerank` pulls `torch` and
  `transformers`; the default install has neither. The reranker's *contract*
  (ordering, score threshold, batch behaviour) is unit-tested against the
  deterministic lexical fallback, and the pipeline is tested with a stub
  reranker. Downloading model weights in CI would add minutes and a flaky
  network dependency for no assertion the stub does not make.
* **Real embedding-model inference.** Same reason. `EMBEDDING_PROVIDER=hashing`
  is the CI path.
* **Outbound HTTP in `search_web_sources`.** The tool is registered only when
  `AGENT_WEB_SEARCH_ENABLED=true`, which is off by default, and the fetch is not
  implemented beyond its allow-list and timeout. Testing it would test a stub.
  The *gate* — that the tool is absent from the registry unless the flag is set —
  is covered in `tests/unit/test_tool_permissions.py`.
* **OCR of scanned PDFs.** Not implemented; an image-only page is recorded as a
  warning, not content. There is no behaviour to assert beyond the warning,
  which `tests/unit/test_parsers.py` covers.
* **The LangGraph agent layer over live traffic through the real provider.**
  The graph is unit-tested node by node and integration-tested with a scripted
  gateway. A live-provider test would be the incident described in §2.
* **Latency thresholds in the default tier.** Wall-clock assertions flake on a
  shared runner. The only timing assertions are generous ones that detect an
  order-of-magnitude regression (for example, readiness probes running
  sequentially rather than concurrently), never a performance target.
* **The frontend.** `make verify-web` covers it; it is a separate toolchain and
  a separate gate.
* **`cli.py` and `main.py`.** argparse plumbing and the uvicorn entry point.
  Their behaviour is demonstrated by running them; a unit test would assert that
  argparse parses.

---

## 5. How to run it

```bash
make test                 # unit only; the fast default
make test-unit            # unit
make test-security        # security
make test-integration     # integration (needs TEST_DATABASE_URL and a live DB)
make test-coverage        # unit + security + integration, coverage.xml, fails
                          # below fail_under (82)
make verify               # format check, lint, typecheck, secret scan, unit,
                          # security, integration, eval gate — cheapest first
```

`make verify` runs the cheap checks first on purpose: a formatting or lint error
should fail in seconds, not after the four-minute integration tier. It does not
require a provider key; `make db-setup` and a running PostgreSQL are the only
external prerequisites.

The eval gate compares `evals/reports/retrieval.json` (committed, no provider
needed) against `evals/reports/baseline.json`. Override with
`make eval-gate EVAL_CURRENT=...`.

### Reproducing a failure locally

```bash
# One test, with the full traceback and captured logs:
cd apps/api && TEST_DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test" \
  ../../.venv/bin/pytest tests/integration/test_tenant_isolation.py::test_tenant_sees_only_its_own_users -vv

# A tier, stopping at the first failure:
cd apps/api && TEST_DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test" \
  ../../.venv/bin/pytest -m integration -x -vv

# Two tiers together (this is what the coverage gate measures):
cd apps/api && TEST_DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test" \
  ../../.venv/bin/pytest -m "unit or security"

# Re-provision the database if the integration tier is failing wholesale:
make db-reset-test && make db-inspect
```

Two traps when reproducing:

1. **Do not run two integration invocations at once.** They share
   `coursellm_test` and the `clean_db` fixture truncates every table between
   tests, so a concurrent run reports failures that do not exist. Run the
   integration tier serially.
2. **`dispose_engine()` runs after every integration test** so a process-wide
   engine cannot carry a tenant GUC or a connection into the next test. If you
   add an integration test that opens its own engine, dispose it in a fixture;
   the `clean_db` fixture only truncates rows.

---

## 6. How to add a test

1. **Pick the tier by dependency, not by subject.** Needs a database? It is
   `integration`. Opens a socket? It is not a `unit` test. Asserts that an
   attack fails? It is `security`, and it should name the control in
   `security.md` that it pins.
2. **Give the module a docstring that says what contract it protects**, and one
   line per non-obvious assertion saying why. The suite is read more often than
   it is run; a test whose reason is not written down gets deleted by the next
   person who sees it fail.
3. **Import settings through a fixture.** Use `test_settings` (unit) or
   `pg_settings` (integration); never construct `Settings()` with the ambient
   environment.
4. **Build any credential-shaped fixture at runtime from fragments.** The secret
   scanner (`scripts/scan_secrets.sh`) runs over tracked files and keeps zero
   exceptions. `"sk-" + "a" * 32` is a fixture; a literal key-shaped string is a
   scanner hit and teaches reviewers to ignore hits.
5. **Do not weaken an existing test to make a change pass.** If a fix breaks a
   test, decide which of the two is wrong and say so in the PR. If the test is
   right and the code is wrong, mark the test `xfail(strict=True)` with the
   defect named, so fixing the code forces the marker to be removed.
6. **Assert the boundary, not a snapshot.** "The response is 201" is weak;
   "the response is 201 and the owner engine sees exactly one row and no other
   tenant's" is the guarantee.

---

## 7. Current counts

Measured on 2026-09-25 on `feat/18-tests-coverage`:

| Tier | Tests | Notes |
|------|------:|-------|
| `unit` | **1264** (1216 before this PR) | `tests/unit/test_core_edges.py` adds 48; `tests/unit/test_config.py` and `test_logging.py` are unchanged |
| `security` | **144** (129 before) | `tests/security/test_hardening.py` adds 15 |
| `integration` | **164** (151 before) | `test_transaction_boundaries.py` adds 8 (1 `xfail`), `test_concurrency.py` adds 5 |
| **selected by `make test-coverage`** | **1572** | 1571 pass, 1 `xfail` — a named defect, not a flake; see COVERAGE.md |

Coverage: 86.1 % of statements, 66.9 % of branches, 82.99 % weighted, against a
`fail_under` of 82. The per-package table and the named uncovered functions are
in [`apps/api/tests/COVERAGE.md`](../apps/api/tests/COVERAGE.md).

`make verify` (the full gate) on 2026-09-24T23:11:23Z–23:15:44Z: format clean
(260 files), lint clean, mypy clean (144 + 11 source files), secret scan clean,
`unit` 1264 passed, `security` 144 passed, `integration` 163 passed + 1 `xfail`,
eval gate "no regressions", exit 0.

## 8. Where the guarantees live

The highest-value tests are not the ones with the most assertions; they are the
ones that would have caught the bugs this project actually had. Four of them are
worth knowing by name:

* `tests/integration/test_transaction_boundaries.py` — ingestion failure leaves
  no orphan row and no orphan object; a failed evaluation leaves no partial
  progress event; deleting a document repairs the BM25 aggregates that no
  foreign key reaches. Each of these was a real defect.
* `tests/integration/test_concurrency.py` — ten concurrent requests from two
  tenants against a pool smaller than the request count. A sequential test
  cannot prove the pooled-connection tenancy guarantee, because it never reuses
  a connection across tenants.
* `tests/integration/test_tenant_isolation.py` — the app role is subject to RLS,
  an unset GUC returns zero rows, and a cross-tenant write is rejected by the
  database rather than by application code.
* `tests/unit/test_core_edges.py` — every retrieval-affecting setting changes
  `retrieval_config_version`. A new retrieval setting that does not change the
  hash is a bug: caches keyed on the old hash would serve results from a
  different configuration.
