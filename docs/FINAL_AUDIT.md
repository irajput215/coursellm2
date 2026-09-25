# Final audit — CourseLLM

This audit is the closing pass of the rebuild. It answers five questions with evidence,
and it records what it could not verify. It is written to be boring: every claim names
the file that implements it, every number names the artefact it comes from, and every
gap is stated rather than implied.

- **Method.** The "objective" is taken to be the capability set in
  `docs/PROJECT_AUDIT.md` §6 (*Missing production capabilities*) — the list of things
  the rebuild exists to add — read alongside the target architecture in
  `docs/architecture/` and the follow-up list for the hardening pass. Requirements are
  walked section by section in §2.
- **Spot-checks.** §3 checks specific architectural claims against the code and records
  every mismatch found and fixed.
- **Numbers.** §4 states which numbers are reproducible and from what.
- **Hygiene.** §5 reports secrets, dead code, unused dependencies and TODO markers.
- **Gaps.** §6 ranks what remains, honestly.

---

## 1. Summary

The rebuild is complete against the §6 capability list: every capability that was
"absent" or "none" now has an implementation with a test, except where §6 names
something the rebuild deliberately did not build (see §6). The four defects the
hardening pass was asked to close are closed, with the tests flipped rather than
weakened:

| Defect | Fix | Test |
|--------|-----|------|
| Question lost on hard generation failure | `services/chat.py::commit_turn`, called by `run_turn`/`prepare_turn`; `db/tenancy.py::tenant_session` owns commit/rollback so a mid-request commit is possible | `tests/integration/test_transaction_boundaries.py` (the `xfail(strict=True)` marker was **removed**; the test passes) |
| `retrieval_config_version` omitted `rerank_min_score` | `core/config.py` payload | `tests/unit/test_core_edges.py::test_rerank_min_score_is_in_the_hash` |
| `redact_event` did not recurse | `core/logging.py::_redact_value` walks dicts/lists with a depth cap | `tests/unit/test_core_edges.py::TestRedactionReachesIntoContainers` (assertions flipped) |
| Configuration that does nothing | Deleted `AGENT_MODEL`/`AGENT_FALLBACK_MODEL`/`AGENT_ROUTER_MODEL` and `GRAPH_MIN_EDGE_CONFIDENCE`; promoted the graph confidence, mastery and recommendation weights to validated `Settings` | `tests/unit/test_config.py`, extraction/planner/recommend tests |

---

## 2. Requirement traceability (`docs/PROJECT_AUDIT.md` §6)

| §6 capability ("as found") | Implementation |
|----------------------------|----------------|
| Multi-tenancy (absent; ad-hoc `user_id` filters) | `db/tenancy.py` (per-transaction GUC), `repositories/base.py` (scope-required), `alembic/versions/*` (`tenant_isolation` policies), `api/deps.py` (token-derived context), `tests/integration/test_tenant_isolation.py`, `tests/security/test_tenancy_boundary.py` |
| Migration discipline (broken by `create_all` at import) | `apps/api/alembic/versions/` (8 revisions), `apps/api/alembic/env.py`, lazy engine in `db/session.py` |
| Rate limiting (none) | `security/ratelimit.py`, `api/deps.py::enforce_request_rate_limit` / `enforce_llm_rate_limit`, `tests/security/test_rate_limit.py` |
| Structured logging (none; `print()`) | `core/logging.py` (structlog + recursive redaction), `tests/unit/test_logging.py` |
| Health / readiness (only `GET /`) | `api/routers/health.py` (`/healthz`, `/readyz` probe the DB and cache) |
| Retries / timeouts / circuit breakers | `llm/gateway.py` (status-code classification, bounded backoff, ordered fallback), `rag/rerank/pipeline.py` (timeout → RRF order), `tests/unit/test_llm_gateway.py` |
| Streaming (none) | `api/routers/chat.py::stream_chat`, `rag/generation/generator.py`, frontend `features/chat/streamChat.ts` |
| Caching (none) | `core/config.py` cache settings, `db/session.py` Redis plumbing, cache bypass on failure |
| Cost / token tracking (tokens logged and discarded) | `llm/cost.py`, `db/models/usage.py::LLMUsage`, `llm/gateway.py::_record`, `tests/integration/test_llm_usage_persistence.py` |
| Observability (traces only `/ask`) | `observability/{tracing,metrics,attributes}.py`, `middleware.py`, `tests/integration/test_tracing_spans.py` |
| Evaluation (entirely absent) | `evals/runners/*`, `evals/metrics/*`, `evals/judges/llm_judge.py`, `evals/reports/baseline.json`, `tests/unit/test_eval_*`, `tests/integration/test_eval_harness_end_to_end.py` |
| Prompt management (inline, unversioned) | `prompts/` (tutor/planner/recommender/assessor/evaluator/safety), `prompts/loader.py`, `tests/unit/test_prompt_loader.py` |
| Knowledge graph (absent) | `graph/{extraction,confidence,repository,schemas}.py`, `db/models/graph.py`, `tools/graph_tools.py`, `tests/unit/test_graph_*`, `tests/integration/test_graph_*` |
| Roadmaps as a feature (a `study_plans` table) | `learning/{planner,adaptation,progress,schemas}.py`, `services/roadmap.py`, `api/routers/roadmaps.py`, `tests/integration/test_roadmap_flow.py` |
| Recommendations (ad-hoc LLM text) | `recommend/{catalogue,ranking,explanation,seed,service}.py`, `db/models/resource.py`, `api/routers/recommendations.py`, `tests/integration/test_recommendation_flow.py` |
| AI security controls (none) | `security/{injection,sanitize,output}.py`, `tools/{registry,permissions}.py`, `tests/security/*` |
| Infrastructure as code (absent) | `infra/terraform/**` (modules + dev/prod roots), `Makefile` `tf-*` targets |
| CI/CD (absent) | `.github/workflows/`, `Makefile` `verify`, `.pre-commit-config.yaml` |
| Container hygiene (unpinned base, toolchain in runtime layer) | `docker/{api,web}.Dockerfile`, `docker/nginx.conf`, `docker-compose.yml` |

**Deliberately not built** (from `PROJECT_AUDIT.md` §11): the prototype's email/calendar
"tool" surface, and the Langfuse-specific observability path (replaced by OpenTelemetry).

---

## 3. Architectural claims checked against the code

Each row is a specific claim; "mismatch" means the claim and the code disagreed and the
fix is recorded.

| # | Claim | Code | Result |
|---|-------|------|--------|
| 1 | `checkpoint_ns = tenant_id` isolates checkpoints | LangGraph 1.2 ignores `checkpoint_ns` for top-level checkpoints; `services/agent.py` uses thread id `{tenant_id}:{conversation_id}` | Documented deviation in `agent-architecture.md` §14.1 — already known, verified |
| 2 | `GRAPH_MIN_TRAVERSABLE_CONFIDENCE` default 0.60 | `Settings.graph_min_traversable_confidence` was **0.55** | **Mismatch fixed**: setting default set to 0.60, matching the doc and `graph/confidence.py` |
| 3 | `GRAPH_STATEMENT_TIMEOUT_MS` default 2000 | `graph_statement_timeout_ms = 1500` | **Mismatch fixed** in the doc table |
| 4 | `GRAPH_EXTRACTION_MAX_CHUNKS_PER_DOC` | `graph_max_extraction_chunks_per_document` | **Mismatch fixed** in the doc table |
| 5 | `GRAPH_EXPANSION_K`, `GRAPH_RETRIEVAL_ENABLED` settings | No such setting, no such feature | **Removed from the doc** and explained |
| 6 | `AGENT_MODEL` etc. pin agent models | Read into metadata only; gateway routes by `ModelTask` | **Deleted the settings**; the model actually resolved is recorded |
| 7 | `AGENT_REQUIRE_WRITE_CONFIRMATION`/`ProposedAction` unwired | `tools/registry.py::_requires_confirmation` and `ToolExecutor.confirm`; `POST /chat/confirm` re-validates | **Stale follow-up**; the flow is wired |
| 8 | No first-class `graph:review` permission | `Permission.GRAPH_REVIEW`, `GRAPH_REVIEW_PERMISSION`, no agent holds it | **Stale follow-up**; it is first-class and tested |
| 9 | LangGraph layer unreachable over HTTP | `services/chat.py::DEFAULT_ENGINE = "agent"`; `_ask_agent` calls `run_turn` | **Stale follow-up**; the graph is the default request path |
| 10 | `messages` lacks observability columns | `db/models/conversation.py` has `intent/model/prompt_version/latency_ms/trace_id`; migration `020bc81dc009` | **Stale follow-up** |
| 11 | `evals/reports/latest.json` not gitignored | `.gitignore` ignores it | **Stale follow-up** |
| 12 | `search_web_sources` degrades with `external_sources_unavailable` | Returns `outbound_fetch_deferred` | **Mismatch fixed** in `agent-architecture.md` §14.2 |
| 13 | Graph design references `tests/graph/*` and `evals/datasets/golden_graph.jsonl` | Tests live under `apps/api/tests/{unit,integration}`; no graph golden set exists | **Mismatch fixed**: real test paths cited; the missing runner/set is stated |
| 14 | `redact_event` "scrubs a top-level string and field" | It now recurses | **Mismatch fixed** (code + tests + docstrings) |
| 15 | Citation schema "intentionally does not expose the quote" | The API returns a bounded `quote`; the frontend renders it | **Mismatch fixed** (schema, service, frontend, docstrings) |
| 16 | SSE contract omits `grounded`/`degraded`/`conversation_id` | `done` carries all three; the client consumes them with no follow-up GET | **Fixed** end to end |
| 17 | `SearchResult` carries no `chunk_index` | It does, populated by both retrievers; the extra chunk query was removed | **Fixed** |
| 18 | `README` lists `mcp_server/` | The directory is empty and would not exist on a clean clone | **Fixed**: removed from the README layout and stated as planned |
| 19 | `quiz_attempts.misconceptions` is `list[str]` | Typed objects after migration `c4f8a1d27b90`; API validates `MisconceptionResponse` | **Matched after the fix** |
| 20 | Committed baseline config version | Baseline recorded `4e535f72cb58`; adding `rerank_min_score` made the current hash `8f3641b0eafb` | **Fixed**: baseline's config field synced, metrics unchanged and verified identical |

Additional checks that passed without change: the RLS policy text and `is_local`
usage; COALESCE/`NULLIF` fail-closed behaviour; BM25 formula versus
`rag/retrieval/lexical.py`; RRF formula versus `rag/fusion/rrf.py`; the confidence
weights summing to 1.0 and the bucket thresholds; the 5×13 permission matrix and the
three unregistered capabilities; the LLM retryable status-code set; `bcrypt` cost 12
and the 72-byte guard; the upload allowlist matching the parsers; the derived
`graph_recursion_limit = 2 * graph_max_steps + 8`.

---

## 4. Numbers

| Number | Source | Reproducible? |
|--------|--------|---------------|
| Retrieval metrics (context precision 0.95, recall 0.9226, fused MRR 0.9464, fused recall@10 0.9881, semantic recall@20 1.0, lexical recall@20 0.9583, MRR@5 1.0, nDCG@5 0.9150, precision@5 0.3643, recall@5 0.9226) | `evals/reports/baseline.json` | **Yes** — `make eval-retrieval` reproduced every non-latency metric byte-for-byte; latency varies and is gated with a wall-clock tolerance |
| Latency percentiles | same | Yes in shape; not exactly (wall clock) |
| Generation/citation/cost metrics | `baseline.json["not_measured"]` | Not measurable in CI (no provider key); stated, not claimed |
| `retrieval_config_version = 8f3641b0eafb` | `Settings.retrieval_config_version` | Yes |
| Dataset SHA `309c4d5c…`, corpus SHA `b072f183…` | report `dataset`/`corpus` | Yes |
| Frontend bundle 712.01 kB raw / 214.86 kB gzip | `npm run build` output | Yes, from this checkout; a build output, not a committed artefact |
| Terraform cost estimates | `infra/terraform/README.md` | Estimates from list prices, **not observed bills**; nothing is deployed |

**Scope caveat that must travel with the retrieval numbers:** they are a
retrieval-only run using the deterministic `hashing` embedder (`hashing-v1`, not a
semantic model) and the lexical-fallback reranker, over 8 documents / 14 chunks and 32
questions (28 answerable, 4 unanswerable). They measure pipeline correctness, not model
quality.

---

## 5. Hygiene findings

**Committed secrets: none found.** `bash scripts/scan_secrets.sh` is clean and uses
**zero pragmas** (credential-shaped test fixtures are assembled from fragments at
runtime). `.env` is gitignored; `.env.example` holds only placeholders. Terraform
secrets are generated with `random_password` and stored in Secrets Manager; the
`.tfvars.example` files contain placeholders.

**Dead code / dead claims found and fixed**

- `mcp_server/` is an empty, untracked directory that the README claimed; the claim is
  removed and the MCP server is described as planned.
- `GRAPH_MIN_EDGE_CONFIDENCE` was a setting with no reader — deleted.
- `AGENT_MODEL`/`AGENT_FALLBACK_MODEL`/`AGENT_ROUTER_MODEL` were recorded but could not
  affect routing — deleted.
- `SPAN_AUTHENTICATION`, `SPAN_TOOL_PREFIX`, `COURSELLM_REQUEST_ID`,
  `COURSELLM_TENANT_ID`, `COURSELLM_USER_ID` and `COURSELLM_STREAM` are declared in
  `observability/attributes.py` but never used. **Reported, not fixed**: wiring the
  tool, ingestion, authentication and server-attribute spans is real work (see §6).
- Two documented `GRAPH_*` knobs with no implementation were removed from the config
  table.
- `evals/datasets/golden_graph.jsonl` and a graph evaluation runner are referenced by
  the design but do not exist; the design now says so.

**Unused dependencies (declared, nothing imports them)**

- `tenacity` (core dependency) — no import anywhere.
- `ragas` extra — no import; reserved for a future adapter (`evals/README.md` says so).
- `mcp` extra — no import; the server does not exist.
These are reported rather than removed so the decision to keep or drop them is explicit;
none is installed by `make install-all` except by name in the extras list.

**TODO markers: 1.** `rag/ingestion/embedders.py:259` — `TODO(PR 8): route through
coursellm.llm once the gateway module exists`. The gateway now exists, so the marker is
stale and should be either actioned or deleted; it is reported here and left in place
because changing an embedder's call path is out of this pass's scope.

**Documentation drift fixed:** knowledge-graph config table and thresholds, agent
architecture known gaps and tools sections, README layout and screenshots note,
`evals/reports/README.md` config version, citation/SSE docstrings, `analyzers`
symmetry note, `tests/COVERAGE.md` redaction note if it still describes the old
behaviour (re-checked).

---

## 6. Remaining honest gaps, ranked

1. **No OCR for scanned PDFs and no `.pptx` parser.** An image-only page is a warning,
   not content. Fix: an OCR extra behind the same parser interface, opt-in per
   deployment.
2. **The evaluation is retrieval-only.** Generation faithfulness/relevance/correctness,
   citation precision/recall/hallucination and token/cost are defined but
   `not_measured` in CI. Fix: a budgeted, cached judge run on a schedule, reported
   separately rather than gating every commit.
3. **The eval corpus is tiny (8 documents / 14 chunks) and the embedder is a hash.**
   The gate has little power and says nothing about semantic quality. Fix: a real
   embedding provider in a nightly eval, with the baseline regenerated under it.
4. **Graph neighbours are not a third fused retrieval list.** Deliberate: unmeasured
   signals are not added. Fix: implement it behind a flag and measure it with (3).
5. **Missing observability spans.** No tool spans, no ingestion spans, no
   authentication span, and the server span lacks request/tenant/user/stream/config
   attributes; several span constants are declared but unused. Fix: wire them in
   `tools/registry.py`, `rag/ingestion/*`, `api/deps.py::get_current_context` and
   `middleware.py`, or delete the constants.
6. **No route-level code splitting; one ~712 kB JS chunk (~215 kB gzip).** Fix:
   `React.lazy` per route, then a dynamically imported highlighter.
7. **`search_web_sources` makes no outbound HTTP.** Fix: a domain allowlist + fetch
   with the same evidence fence and output validation.
8. **`search_course` returns documents, not lecture/topic structure.** Fix: a course
   structure schema.
9. **Terraform has never been applied.** No live deployment, no operational evidence.
   Fix: apply to a sandbox account and record a plan/apply in CI.
10. **Unused dependency declarations** (`tenacity`, `ragas`, `mcp`) and one stale
    `TODO`. Fix: remove or action.
11. **No per-tenant spend cap** (only per-turn cost and per-hour request ceilings), and
    no partitioning/archival for the growing ledger tables.

---

## 7. What could not be verified here

- **A live LLM path.** No provider key is configured, so generation, citation and cost
  behaviour is exercised only with scripted/echo gateways and unit tests. The
  `not_measured` entries in the baseline are the honest marker of this.
- **A real embedding/cross-encoder model.** `sentence-transformers`/`torch` are optional
  extras that were not installed, so `HashingEmbedder` and `LexicalReranker` are what
  ran.
- **Terraform apply.** Only format/validate/plan-level checks; no AWS account was
  touched.
- **A browser.** The frontend tests run in jsdom with MSW; no real browser or
  screenshot capture was performed, which is why the README says screenshots are
  pending.
- **The full `make verify` gate in one uninterrupted run.** The tiers were run
  individually and serially (`format`, `lint`, `typecheck`, `secrets-scan`, unit,
  security, integration, `eval-gate`) plus `test-web`; see the PR report for the exact
  counts.
