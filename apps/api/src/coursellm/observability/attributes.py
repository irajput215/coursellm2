"""The span attribute schema, as constants and as policy.

Every attribute a span may carry is named here exactly once, and
``docs/architecture/observability.md`` section 2.2 is the normative list. Call
sites import these constants rather than writing string literals, so a typo is a
``NameError`` in review rather than a silently unqueryable key in a trace
backend.

The module also owns the redaction policy. Section 2.3 is explicit that raw
prompts, document passages, completions and PII are **never** exported as span
attributes; the constants that legitimately contain a word like ``prompt`` or
``name`` are the metadata labels the document itself mandates
(``coursellm.llm.prompt_version`` is a prompt *version*, not prompt text, and
``graph.name``/``db.collection.name`` are identifiers). Everything else that
looks like payload is refused by :func:`sanitize_attributes`, which is applied
inside :func:`coursellm.observability.tracing.span`, so a call site cannot
bypass the control by accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# Span names
# ---------------------------------------------------------------------------
# The HTTP server span is created by ``opentelemetry-instrumentation-fastapi``
# and is named ``{method} {route}`` by that library, so it has no constant here.
SPAN_AUTHENTICATION = "authentication"
SPAN_QUERY_SAFETY = "query_safety_scan"
SPAN_AGENT_GRAPH = "agent_graph"
SPAN_NODE_PREFIX = "node"
SPAN_RETRIEVAL = "retrieval"
SPAN_RETRIEVAL_SEMANTIC = "retrieval.semantic"
SPAN_RETRIEVAL_LEXICAL = "retrieval.lexical"
SPAN_RETRIEVAL_FUSION = "retrieval.fusion"
SPAN_RETRIEVAL_RERANK = "retrieval.rerank"
SPAN_LLM_CALL = "llm_call"
SPAN_GENERATION = "generation"
SPAN_OUTPUT_VALIDATION = "output_validation"
SPAN_DB_QUERY = "db.query"
SPAN_TOOL_PREFIX = "tool"

# ---------------------------------------------------------------------------
# Attribute names (docs/architecture/observability.md section 2.2)
# ---------------------------------------------------------------------------
# -- HTTP server span -------------------------------------------------------
HTTP_REQUEST_METHOD = "http.request.method"
HTTP_ROUTE = "http.route"
HTTP_RESPONSE_STATUS_CODE = "http.response.status_code"
URL_PATH = "url.path"
COURSELLM_REQUEST_ID = "coursellm.request_id"
COURSELLM_STREAM = "coursellm.stream"
COURSELLM_TENANT_ID = "coursellm.tenant_id"
COURSELLM_USER_ID = "coursellm.user_id"
COURSELLM_CONFIG_VERSION = "coursellm.config_version"

# -- Authentication span ----------------------------------------------------
AUTH_METHOD = "auth.method"
AUTH_RESULT = "auth.result"
AUTH_ROLES = "auth.roles"

# -- Agent graph run span ---------------------------------------------------
GRAPH_NAME = "graph.name"
GRAPH_INTENT = "graph.intent"
GRAPH_STEPS = "graph.steps"
GRAPH_TOOL_CALLS = "graph.tool_calls"
GRAPH_DEGRADED = "graph.degraded"
GRAPH_OUTCOME = "graph.outcome"

# -- Per-node span ----------------------------------------------------------
GRAPH_NODE = "graph.node"
GRAPH_NODE_ITERATION = "graph.node.iteration"
GRAPH_NODE_TOOL_CALLS = "graph.node.tool_calls"
GRAPH_NODE_TOKENS_IN = "graph.node.tokens_in"
GRAPH_NODE_TOKENS_OUT = "graph.node.tokens_out"
GRAPH_NODE_RETRY_COUNT = "graph.node.retry_count"

# -- Retrieval spans --------------------------------------------------------
RETRIEVAL_STAGE = "retrieval.stage"
RETRIEVAL_TOP_K_PER_RETRIEVER = "retrieval.top_k_per_retriever"
RETRIEVAL_CANDIDATES_IN = "retrieval.candidates_in"
RETRIEVAL_CANDIDATES_OUT = "retrieval.candidates_out"
RETRIEVAL_RRF_K = "retrieval.rrf_k"
RETRIEVAL_RERANK_TOP_K = "retrieval.rerank_top_k"
RETRIEVAL_FILTERS = "retrieval.filters"
RETRIEVAL_FUSED = "retrieval.fused"
RETRIEVAL_DEGRADED = "retrieval.degraded"
RETRIEVAL_DURATION_MS = "retrieval.duration_ms"

# -- DB query span ----------------------------------------------------------
DB_SYSTEM = "db.system"
DB_OPERATION_NAME = "db.operation.name"
DB_COLLECTION_NAME = "db.collection.name"
DB_QUERY_SUMMARY = "db.query.summary"
DB_ROWS_AFFECTED = "db.rows_affected"
DB_TENANT_SCOPED = "db.tenant_scoped"
DB_RLS_ACTIVE = "db.rls_active"

# -- LLM call span (gen_ai.* semantic conventions) --------------------------
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
COURSELLM_LLM_PROVIDER = "coursellm.llm.provider"
COURSELLM_LLM_COST_USD = "coursellm.llm.cost_usd"
COURSELLM_LLM_RETRY_COUNT = "coursellm.llm.retry_count"
COURSELLM_LLM_FALLBACK_USED = "coursellm.llm.fallback_used"
COURSELLM_LLM_CACHE_HIT = "coursellm.llm.cache_hit"
COURSELLM_LLM_PROMPT_VERSION = "coursellm.llm.prompt_version"

#: Every attribute name the specification permits. The integration test walks a
#: recorded trace and asserts every attribute key it finds is a member.
ALLOWED_ATTRIBUTE_NAMES: frozenset[str] = frozenset(
    {
        HTTP_REQUEST_METHOD,
        HTTP_ROUTE,
        HTTP_RESPONSE_STATUS_CODE,
        URL_PATH,
        COURSELLM_REQUEST_ID,
        COURSELLM_STREAM,
        COURSELLM_TENANT_ID,
        COURSELLM_USER_ID,
        COURSELLM_CONFIG_VERSION,
        AUTH_METHOD,
        AUTH_RESULT,
        AUTH_ROLES,
        GRAPH_NAME,
        GRAPH_INTENT,
        GRAPH_STEPS,
        GRAPH_TOOL_CALLS,
        GRAPH_DEGRADED,
        GRAPH_OUTCOME,
        GRAPH_NODE,
        GRAPH_NODE_ITERATION,
        GRAPH_NODE_TOOL_CALLS,
        GRAPH_NODE_TOKENS_IN,
        GRAPH_NODE_TOKENS_OUT,
        GRAPH_NODE_RETRY_COUNT,
        RETRIEVAL_STAGE,
        RETRIEVAL_TOP_K_PER_RETRIEVER,
        RETRIEVAL_CANDIDATES_IN,
        RETRIEVAL_CANDIDATES_OUT,
        RETRIEVAL_RRF_K,
        RETRIEVAL_RERANK_TOP_K,
        RETRIEVAL_FILTERS,
        RETRIEVAL_FUSED,
        RETRIEVAL_DEGRADED,
        RETRIEVAL_DURATION_MS,
        DB_SYSTEM,
        DB_OPERATION_NAME,
        DB_COLLECTION_NAME,
        DB_QUERY_SUMMARY,
        DB_ROWS_AFFECTED,
        DB_TENANT_SCOPED,
        DB_RLS_ACTIVE,
        GEN_AI_SYSTEM,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_OPERATION_NAME,
        GEN_AI_USAGE_INPUT_TOKENS,
        GEN_AI_USAGE_OUTPUT_TOKENS,
        GEN_AI_RESPONSE_FINISH_REASONS,
        COURSELLM_LLM_PROVIDER,
        COURSELLM_LLM_COST_USD,
        COURSELLM_LLM_RETRY_COUNT,
        COURSELLM_LLM_FALLBACK_USED,
        COURSELLM_LLM_CACHE_HIT,
        COURSELLM_LLM_PROMPT_VERSION,
    }
)

#: Every attribute name defined as a module-level constant, materialised so the
#: unit test can enumerate the schema without importing private names.
ATTRIBUTE_NAMES: tuple[str, ...] = (
    HTTP_REQUEST_METHOD,
    HTTP_ROUTE,
    HTTP_RESPONSE_STATUS_CODE,
    URL_PATH,
    COURSELLM_REQUEST_ID,
    COURSELLM_STREAM,
    COURSELLM_TENANT_ID,
    COURSELLM_USER_ID,
    COURSELLM_CONFIG_VERSION,
    AUTH_METHOD,
    AUTH_RESULT,
    AUTH_ROLES,
    GRAPH_NAME,
    GRAPH_INTENT,
    GRAPH_STEPS,
    GRAPH_TOOL_CALLS,
    GRAPH_DEGRADED,
    GRAPH_OUTCOME,
    GRAPH_NODE,
    GRAPH_NODE_ITERATION,
    GRAPH_NODE_TOOL_CALLS,
    GRAPH_NODE_TOKENS_IN,
    GRAPH_NODE_TOKENS_OUT,
    GRAPH_NODE_RETRY_COUNT,
    RETRIEVAL_STAGE,
    RETRIEVAL_TOP_K_PER_RETRIEVER,
    RETRIEVAL_CANDIDATES_IN,
    RETRIEVAL_CANDIDATES_OUT,
    RETRIEVAL_RRF_K,
    RETRIEVAL_RERANK_TOP_K,
    RETRIEVAL_FILTERS,
    RETRIEVAL_FUSED,
    RETRIEVAL_DEGRADED,
    RETRIEVAL_DURATION_MS,
    DB_SYSTEM,
    DB_OPERATION_NAME,
    DB_COLLECTION_NAME,
    DB_QUERY_SUMMARY,
    DB_ROWS_AFFECTED,
    DB_TENANT_SCOPED,
    DB_RLS_ACTIVE,
    GEN_AI_SYSTEM,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_OPERATION_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    COURSELLM_LLM_PROVIDER,
    COURSELLM_LLM_COST_USD,
    COURSELLM_LLM_RETRY_COUNT,
    COURSELLM_LLM_FALLBACK_USED,
    COURSELLM_LLM_CACHE_HIT,
    COURSELLM_LLM_PROMPT_VERSION,
)

# ---------------------------------------------------------------------------
# Redaction policy (section 2.3)
# ---------------------------------------------------------------------------
#: Substrings that mark an attribute as payload rather than metadata. The list
#: is deliberately blunt: a false positive costs one debugging attribute, a
#: false negative writes a student's document into a trace backend.
FORBIDDEN_ATTRIBUTE_SUBSTRINGS: tuple[str, ...] = (
    "prompt",
    "message",
    "content",
    "question",
    "answer",
    "email",
    "name",
)

#: Documented attributes whose names contain a forbidden substring but whose
#: *values* are identifiers or versions, never payload. Each entry is justified
#: by the document's own attribute table; adding one requires the same.
METADATA_NAME_EXCEPTIONS: frozenset[str] = frozenset(
    {
        GRAPH_NAME,
        DB_COLLECTION_NAME,
        DB_OPERATION_NAME,
        GEN_AI_OPERATION_NAME,
        COURSELLM_LLM_PROMPT_VERSION,
    }
)

#: The redaction filter runs against a key only when the key is not one of the
#: documented schema names above, so version/identifier attributes survive while
#: a rogue ``prompt_text`` or ``document_content`` key is dropped.
_FORBIDDEN_PAYLOAD_KEYS: tuple[str, ...] = (
    "prompt_text",
    "prompt",
    "messages",
    "message_content",
    "content",
    "document",
    "document_text",
    "chunk_text",
    "question",
    "answer",
    "completion",
    "email",
    "full_name",
    "student_id",
    "authorization",
    "api_key",
    "token",
    "credentials",
)


def forbidden_substrings(name: str) -> tuple[str, ...]:
    """Which forbidden substrings ``name`` matches, case-insensitively."""
    lowered = name.lower()
    return tuple(pattern for pattern in FORBIDDEN_ATTRIBUTE_SUBSTRINGS if pattern in lowered)


def is_forbidden_payload_key(key: str) -> bool:
    """Whether an attribute key would carry payload and must be dropped.

    Documented schema names are always allowed: ``coursellm.llm.prompt_version``
    is a version label and is required by section 2.2, while an undocumented
    ``prompt_text`` is not.
    """
    if key in ALLOWED_ATTRIBUTE_NAMES or key in METADATA_NAME_EXCEPTIONS:
        return False
    lowered = key.lower()
    return any(fragment in lowered for fragment in _FORBIDDEN_PAYLOAD_KEYS)


def sanitize_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Drop every attribute whose key would carry prompt, document or PII text.

    Unknown keys are checked against :data:`_FORBIDDEN_PAYLOAD_KEYS`; known
    schema keys are passed through untouched. The returned mapping is what
    actually reaches the tracer, so redaction is enforced at the boundary rather
    than at each call site.
    """
    return {key: value for key, value in attributes.items() if not is_forbidden_payload_key(key)}
