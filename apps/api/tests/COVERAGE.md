# Test coverage: measurement, threshold and the gaps that matter

Measured with:

```
cd apps/api
TEST_DATABASE_URL="postgresql+asyncpg://coursellm_app@localhost:5432/coursellm_test" \
  ../../.venv/bin/pytest -m "unit or security or integration" \
  --cov=coursellm --cov-report=term-missing --cov-report=xml
```

Date of the measurement below: **2026-09-25**, branch coverage on, on the
`feat/18-tests-coverage` branch. `apps/api/pyproject.toml` sets `fail_under = 82`,
so `make test-coverage` fails the build if the suite drops below that.

## The number

| | before PR 18 | after PR 18 |
|---|---:|---:|
| Statements covered | 9324 / 11047 = **84.4 %** | 9507 / 11047 = **86.1 %** |
| Branches covered | 1166 / 2102 = **55.5 %** | 1406 / 2102 = **66.9 %** |
| `coverage.py` total (branches weighted into the denominator) | **81 %** | **83 %** (82.99 %) |

The total is the figure `fail_under` compares against — `(lines + branches)`
covered over `(lines + branches)` measured, not the statement percentage. The
per-package table is statement coverage, which is the number a reviewer can act
on; the branch figure is in the total.

Why the threshold is `82` and not `83`: the measured total is 82.99 %, and
`fail_under` does not round. At this code size one point is roughly 130
statements, so a one-point margin is not decorative — deleting the
`sanitize_query` and `neutralise_markers` tests added in this PR would take the
total below it. `83` would fail the run it is meant to guard; `80` would let a
module go dark without anyone noticing.

## Per-package breakdown (statement coverage)

| Package | Statements | Missed | Covered |
|---|---:|---:|---:|
| `core` | 324 | 1 | 99.7 % |
| `db` | 593 | 27 | 95.4 % |
| `security` | 523 | 31 | 94.1 % |
| `graph` | 868 | 80 | 90.8 % |
| `prompts` | 89 | 8 | 91.0 % |
| `agents` | 1420 | 153 | 89.2 % |
| `rag` | 1396 | 163 | 88.3 % |
| `llm` | 452 | 58 | 87.2 % |
| `observability` | 600 | 77 | 87.2 % |
| `storage` | 91 | 12 | 86.8 % |
| `learning` | 547 | 78 | 85.7 % |
| `api` | 1047 | 174 | 83.4 % |
| `assessment` | 661 | 125 | 81.1 % |
| `recommend` | 454 | 89 | 80.4 % |
| `tools` | 725 | 147 | 79.7 % |
| `repositories` | 150 | 32 | 78.7 % |
| `services` | 946 | 211 | 77.7 % |
| `coursellm` (top-level `__init__`, `cli`, `main`) | 161 | 74 | 54.0 % |

Modules that moved to **100 %** in this PR and are worth naming because they are
controls, not plumbing: `core/config.py`, `core/errors.py`,
`security/sanitize.py`, `security/tokens.py`, `middleware.py`.

## The gaps that matter

Uncovered lines and uncovered *behaviour* are different things. Entry 0 below is
100 %-covered code with a hole in what it guarantees; the rest are lines no test
executes. Each entry says what the code does, whether the behaviour is exercised
*somewhere else* (so the uncovered lines are the local arithmetic rather than the
guarantee), and why.

### 0. `core/config.py::retrieval_config_version` — one setting is missing, and it is a bug

The hash payload covers eleven fields and omits **`rerank_min_score`**, which is
the floor that drops weak passages in `rag/rerank/pipeline.py`. Changing it
changes which evidence survives, so a cache entry keyed on the old hash is not
the same result as one produced under the new floor — exactly the
unattributable-quality-shift the version exists to prevent.

`tests/unit/test_core_edges.py::TestRetrievalConfigVersionCoversEveryRetrievalSetting::test_rerank_min_score_is_not_in_the_hash_today`
asserts the current behaviour and says, in the docstring, that the fix is to add
`"rerank_min_score": self.rerank_min_score` to the payload and flip the
assertion. It is written this way because the fix is a source change outside
this PR's sanctioned edit set (`Makefile` and `pyproject.toml` only). Everything
else retrieval-affecting is covered by the parameterised table in the same
class, and `test_rerank_operational_knobs_are_not_in_the_hash` records why
timeout and batch size correctly stay out.

### 1. `services/chat.py` — proposal confirmation and history helpers — **not a gap, verified**

Two regions are uncovered:

* 588–608 is `confirm_proposal`, the out-of-band execution of a withheld write.
  The path itself is exercised in `test_chat_agent_path.py` through the chat
  route; the uncovered lines are the construction of the executor inside the
  service, which is duplicated by the route-level fixture. Worth an integration
  test of `POST /chat/confirm` with a real signed token, but that is a product
  surface the brief did not ask for, and the permission re-check it performs is
  covered directly in `test_tool_permissions.py`.
* 628–636 is `_resolve_conversation`'s branch for a conversation id that is not
  the caller's. It is covered for the *loaded* case; the cross-tenant 404 is
  asserted in `test_documents_api.py::test_another_users_document_in_the_same_tenant_is_404`
  for the equivalent document path and in `test_tenant_isolation.py` at the SQL
  layer. Not worth a duplicate.

The streaming assembly (the `GeneratedAnswer` branches inside the SSE
generator) is exercised end to end by
`test_chat_flow.py::test_streaming_endpoint_emits_tokens_citations_and_done`
and `test_chat_agent_path.py::TestStreaming`. The SSE contract is the behaviour,
and it is already asserted.

### 2. `recommend/service.py::_resolve_gaps` (± 40 lines) — **worth covering next, not in this PR**

The roadmap-derived branch (194–233) resolves gaps from a roadmap's remaining
steps before falling back to weak mastery. The fallback branch is covered
(`test_gap_detection_falls_back_to_mastery_without_a_roadmap`); the roadmap
branch is not, because every recommendation integration test seeds mastery
directly rather than through a roadmap. A bug in the precedence order — for
example, returning an empty roadmap gap set instead of falling through — would
be invisible.

Not fixed here because the fix is a new integration fixture (a roadmap with
unmastered steps), not a line of coverage, and the brief's priority is the
transaction boundaries. It is the single largest genuine hole in
`recommend/service.py` and should be the first test added after this PR.

### 3. `services/roadmap.py` (~92 lines) — **deliberately not covered**

The uncovered regions are the adaptation and revision paths
(`_apply_adaptation`, revision scheduling, the velocity helpers). Every public
entry point is driven end to end by `test_roadmap_flow.py`; what is uncovered is
the branch structure *inside* those paths. Unit-testing each branch would mean
re-implementing the integration fixtures with mocks, which the project's policy
(`docs/testing.md`) rejects: a mocked roadmap projection proves the mock, not
the projection.

### 4. `cli.py`, `main.py` — **not worth covering**

`cli.py` is argparse plumbing for `db-inspect` and `seed-catalogue`, and
`main.py` is the uvicorn entry point. Both are exercised by hand and by
`make db-inspect`; a unit test would assert that argparse parses, which the
`--help` exit code already proves. `main.py`'s `if __name__ == "__main__"` guard
is excluded by the `exclude_lines` pattern in `pyproject.toml`.

### 5. `rag/ingestion/embedders.py` (36 lines missed) and `parsers.py` (30) — **deliberately not covered**

The missed lines are the `sentence-transformers` and `python-docx` code paths.
The default install does not include either extra (`apps/api/pyproject.toml`
`[project.optional-dependencies]`), and CI runs the hashing embedder and the PDF
and text parsers. Tokenising a real PDF page and loading an ONNX model in CI
would add minutes and a download for no assertion the deterministic path does
not already make. The boundary — that a missing extra produces a typed error
rather than an ImportError at request time — is covered in
`test_document_validation.py`.

### 6. `llm/gateway.py` streaming (312–398) — **partially covered, gap named**

`complete()` is fully covered, including retries, fallback and usage
persistence. The uncovered lines are the *streaming* fallback chain: the
integration suite streams only through the scripted double, which overrides
`stream` entirely, so the real gateway's per-model retry loop never runs. This
is the one place where a real bug (a fallback that streams the wrong model)
would not be caught. It is not covered here because it needs a streaming
provider double that does not exist yet; it is recorded so the next person does
not assume the stream path is tested.

### 7. `tools/quiz.py` and `tools/recommend.py` handler bodies — **covered indirectly**

The handler functions (`create_quiz`, `evaluate_answer`, `get_recommendations`)
are dispatched through `tools/registry.py`, whose permission matrix and argument
validation are covered by `test_tool_permissions.py` and
`test_tool_executor.py`, and whose services are covered by the assessment and
recommendation integration suites. The uncovered lines are the handlers' own
happy paths, reachable only with a fully constructed `ToolContext`. A unit test
would either mock the service (proving nothing) or duplicate the integration
fixture. Left uncovered on purpose.

### 8. `security/output.py` lines 138, 149, 168 — **three defensive branches**

These are `except` branches for a citation id that has already been validated
and a regex that has already matched. They are unreachable without corrupting an
in-memory object, which is a test of the test. Not worth a `pragma: no cover`:
the module is a control read by reviewers, and four names in `exclude_lines`
already covers the two genuine structural cases.

### 9. `core/logging.py` line 106 — **unreachable by construction**

The line is the JSON renderer branch reached only when `log_format == "json"`.
It *is* exercised by the production configuration, but the test suite runs with
the console renderer so the error output is readable. A test that asserted the
renderer choice would add nothing; the interesting assertion — that a credential
never survives into the emitted event — is in `test_logging.py` and is
renderer-independent because `redact_event` runs before either renderer.

## Deliberate `pragma: no cover` additions

None. Every uncovered line above is either genuinely unreachable (covered by the
existing `exclude_lines` patterns), deliberately deferred with a reason, or a
module that should be measured because someone will edit it. Adding a pragma to
make a number move is the failure mode this document is written to avoid.

## Known defect recorded by a failing-by-design test

`tests/integration/test_transaction_boundaries.py::TestAFailedTurnStillRecordsTheQuestion::test_the_user_message_survives_a_hard_generation_failure`
is `xfail(strict=True)`.

`services/agent.py::run_turn` stages the user message and then invokes the graph
inside the request's single transaction. An unhandled graph failure rolls the
question back with the answer, so the user's transcript loses a question that
was asked. The fix is a commit boundary for the user message before generation —
a source change outside this PR's sanctioned edit set. `strict=True` means the
moment the fix lands the test `XPASS`es and the marker must be removed.
