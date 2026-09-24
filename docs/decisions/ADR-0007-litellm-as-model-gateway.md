# ADR-0007: LiteLLM as the model gateway

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0001, ADR-0008, ADR-0010

## Context

The prototype called provider SDKs directly and inconsistently. `services/llm.py`
constructed `OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")`
and hard-coded both the model (`openai/gpt-oss-safeguard-20b`) and a placeholder
system prompt. `rag/generation/generator.py` used a different abstraction, a
`pydantic_ai` agent pinned to `groq:llama-3.3-70b-versatile`. `requirements.txt`
pinned `anthropic` with no call site. There was no retry policy, no fallback, no
spend accounting, no per-call model/provider record, and no consistent
structured-output contract; provider choice was implicit in whichever module made
the call.

The platform needs several behaviours once rather than per call site: a single
interface where code names a **task**, not a vendor model; retries with backoff
and ordered fallbacks; token and cost accounting attributable to a tenant;
structured outputs normalised across providers from the shared Pydantic schema
layer (ADR-0001); and the option to run in-process or as a standalone gateway
without rewriting call sites.

## Decision

Use **LiteLLM** as the single model gateway.

- Code requests a **logical task** (`tutor.answer`, `intent.route`,
  `quiz.generate`, `embed`, `rerank`), never a provider model id. Routing maps a
  task to a primary model and an ordered fallback list.
- Retries use bounded exponential backoff with jitter and apply only to retryable
  errors (timeouts, 429, 5xx); non-retryable errors (validation, auth,
  context-length) fail fast.
- Every call writes an `LLM_USAGE` row (provider, model, task, tokens, latency,
  computed cost, tenant, request id) as required by
  `docs/architecture/system.md` §8.
- Structured outputs use the shared Pydantic models; the gateway normalises
  provider-specific mechanisms and validates the returned object against the
  schema.
- LiteLLM runs **in-process** by default; the same configuration can be deployed
  as a **standalone gateway** when central key management and routing are wanted.
  The deployment doc records the switch.

### Graceful degradation ladder

1. **Primary model** for the task.
2. **Fallback model(s)** in configured order, each with its own timeout and retry
   budget; the response records which route answered.
3. **Extractive answer** from retrieved context — no generation at all: the
   top-ranked passages are returned as ranked excerpts with citations, marked
   degraded. The system never answers from parametric memory
   (`docs/architecture/rag.md` §8). With no evidence, the ladder ends in refusal.

The user-visible banners are defined in `docs/architecture/system.md` §6, and the
ladder is enforced by `apps/api/tests/test_degradation.py`.

## Consequences

### Positive

- Provider SDKs disappear from application code, and provider failure becomes a
  configuration question rather than a code path per call site.
- Task-based routing decouples product intent from vendor model names, so a model
  can be swapped or upgraded without touching domain code.
- Retries, fallbacks, timeouts, and spend accounting are implemented once and
  behave consistently everywhere.
- Cost and tokens are attributable per tenant and per task, enabling budget
  enforcement and cost-regression investigation.
- Structured outputs are validated against the same Pydantic models used for HTTP,
  so LLM contract drift is a validation error, not downstream corruption.
- The optional gateway mode centralises keys and routing without an application
  rewrite.

### Negative

- **An extra abstraction layer.** Provider errors are wrapped, so debugging a raw
  400 or a malformed stream means unwrapping LiteLLM's exception types, and some
  provider response fields are not surfaced.
- **Version coupling.** LiteLLM iterates quickly and its streaming,
  structured-output, and fallback behaviour changes between releases; the
  dependency must be pinned and upgrades need contract tests, not version bumps.
- **Less access to provider-proprietary features.** Native tool-calling quirks,
  prompt-caching controls, reasoning/thinking parameters, and vendor safety
  options are exposed late or not at all; an essential feature may need a
  documented direct-SDK escape hatch, which is a leak in the abstraction.
- **Gateway mode is another hop and a potential single point of failure**, adding
  latency, a deploy unit, and a health check; in-process mode avoids that but
  duplicates configuration across replicas.
- **Cost estimates are only as good as the price table.** Computed cost can drift
  from the invoice when providers change prices or token categories, so figures
  are operational estimates reconciled periodically.
- Streaming with fallback and structured output is a known bug source: a fallback
  cannot cleanly resume mid-stream, so fallback applies before the first token.

### Neutral

- LiteLLM is a library, not a datastore; it introduces no new state to back up.
- `LLM_USAGE` is the system of record for spend, not LiteLLM.
- Provider tuning (temperature, max tokens, timeouts) stays in configuration and
  is part of the evaluated configuration.

## Alternatives considered

- **Direct provider SDKs (prototype).** Fewest layers and full provider access,
  but retry, fallback, cost, and structured-output logic is spread across every
  call site and the vendor is hard-coded. Rejected.
- **LangChain model wrappers.** Adjacent because of LangGraph (ADR-0008), but using
  them for provider abstraction couples the LLM interface to the orchestration
  framework's release cycle. Not chosen; orchestration and model access stay
  separable.
- **A hosted router (OpenRouter and similar).** Convenient and broad, but it adds a
  per-token margin, constrains self-hosted or private models, and gives less
  control over routing and data residency. Rejected.
- **A self-hosted gateway (Portkey or in-house).** Viable, and some offer richer
  observability, but they duplicate capabilities already present (OpenTelemetry,
  LangSmith) and add a service to operate. LiteLLM in-process covers the current
  need; standalone mode is the escape hatch.
- **A hand-written adapter layer.** Maximum control, but it reimplements retries,
  fallbacks, provider normalisation, and pricing. Rejected.

## How this is verified

- `apps/api/tests/test_degradation.py` drives a fake provider that fails the
  primary, then the fallbacks, and asserts the extractive path and degraded markers
  at each step.
- A startup contract test asserts every configured task resolves to a primary and,
  where required, a fallback, so a missing route fails fast rather than at request
  time.
- Every model call is asserted to produce an `LLM_USAGE` row with a resolvable
  tenant and request id.
- A lint rule forbids importing provider SDKs in domain and service code; any
  escape hatch is isolated in the gateway adapter with a review note.
- `evals/` records the resolved provider and model per run (ADR-0010), so a
  quality change can be attributed to a model change rather than guessed.
