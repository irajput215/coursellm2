"""The bounded agent graph: topology, edges and dependency wiring.

``docs/architecture/agent-architecture.md`` section 3 is the specification. The
spine is fixed in code; the model's freedom is confined to two decisions — which
routed capability handles the turn, and which tool to declare — and both are
checked by pure conditional edges before anything is spent.

The builder takes its collaborators as arguments rather than reaching for
globals, which is what makes the end-to-end test able to substitute a scripted
gateway, a deterministic reranker, an in-memory knowledge graph and an
``InMemorySaver`` while still running the production topology.
"""

from __future__ import annotations

import uuid
from collections.abc import Hashable
from functools import partial
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.agents.nodes import NodeFn
from coursellm.agents.nodes.agents import ROLES, make_agent_node
from coursellm.agents.nodes.answer_composer import (
    CourseNameProvider,
    MetadataProvider,
    make_answer_composer_node,
)
from coursellm.agents.nodes.intent_router import make_intent_router_node
from coursellm.agents.nodes.knowledge_graph import (
    GraphSearch,
    make_knowledge_graph_node,
)
from coursellm.agents.nodes.plan_retrieval import ScopeCheck, make_plan_retrieval_node
from coursellm.agents.nodes.rerank import make_rerank_node
from coursellm.agents.nodes.retrieval import RetrieveFn, make_retrieval_node
from coursellm.agents.nodes.safety_guardrail import make_safety_guardrail_node
from coursellm.agents.nodes.student_context import (
    ProgressProvider,
    make_student_context_node,
)
from coursellm.agents.nodes.tools import make_tools_node
from coursellm.agents.routing import (
    COMPOSE,
    intent_agent_node,
    route_after_agent,
    route_after_evidence,
    route_intent,
)
from coursellm.agents.state import ConversationState
from coursellm.core.config import Settings
from coursellm.db.tenancy import TenantScope
from coursellm.llm import LLMGateway
from coursellm.observability import langsmith, tracing
from coursellm.observability.attributes import (
    GRAPH_NODE,
    GRAPH_NODE_ITERATION,
    GRAPH_NODE_RETRY_COUNT,
    GRAPH_NODE_TOKENS_IN,
    GRAPH_NODE_TOKENS_OUT,
    GRAPH_NODE_TOOL_CALLS,
    SPAN_NODE_PREFIX,
)
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import DocumentMeta
from coursellm.rag.rerank.rerankers import Reranker
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.tools import ToolRegistry, build_tool_registry
from coursellm.tools.graph_tools import KnowledgeGraphRepository
from coursellm.tools.registry import ToolExecutor

#: Conditional-edge return value -> graph node name. Keys are ``Hashable`` to
#: match LangGraph's ``path_map`` signature (a ``dict[str, str]`` is invariant
#: and therefore not assignable).
_INTENT_TARGETS: dict[Hashable, str] = {intent: intent_agent_node(intent) for intent in ROLES}
_AFTER_AGENT_TARGETS: dict[Hashable, str] = {
    "retrieval": "plan_retrieval",
    "tools": "tools",
    "compose": COMPOSE,
}
_AFTER_EVIDENCE_TARGETS: dict[Hashable, str] = {
    **{intent_agent_node(intent): intent_agent_node(intent) for intent in ROLES},
    COMPOSE: COMPOSE,
}


def _add_node(builder: StateGraph[ConversationState], name: str, node: NodeFn) -> None:
    """Add a node, widening the callable for LangGraph's Protocol-based overloads.

    ``add_node`` resolves its ``_Node`` protocol through a method-level generic;
    a ``Callable`` alias returned by a factory does not infer that TypeVar under
    mypy strict. The runtime contract is unchanged and is asserted by the graph
    tests; this cast is purely to satisfy the checker.
    """
    builder.add_node(name, cast(Any, node))


def _observed_node(
    name: str,
    node: NodeFn,
    *,
    langsmith_handle: langsmith.LangSmithHandle,
    config_version: str,
) -> NodeFn:
    """Wrap a node so every execution emits a span and a LangSmith run.

    The span records the node name, its iteration and the token/tool deltas the
    node returned. It records nothing about the state's text: ``graph.node`` is
    an identifier and :func:`coursellm.observability.tracing.span` drops any
    attribute whose key looks like payload.
    """

    async def observed(state: ConversationState) -> dict[str, Any]:
        iteration = int(state.get("iteration_count", 0) or 0)
        with tracing.span(
            f"{SPAN_NODE_PREFIX}:{name}",
            **{GRAPH_NODE: name, GRAPH_NODE_ITERATION: iteration},
        ) as record:
            with langsmith.trace_node(
                langsmith_handle,
                graph="tutor_graph",
                node=name,
                config_version=config_version,
                metadata={"iteration": iteration},
            ):
                update = await node(state)
            _record_node_update(record, update)
            return update

    return observed


def _record_node_update(record: tracing.SpanRecorder, update: dict[str, Any]) -> None:
    usage = update.get("token_usage")
    if isinstance(usage, dict):
        record.set_attributes(
            {
                GRAPH_NODE_TOKENS_IN: int(usage.get("prompt_tokens", 0) or 0),
                GRAPH_NODE_TOKENS_OUT: int(usage.get("completion_tokens", 0) or 0),
            }
        )
    tool_calls = update.get("tool_calls")
    if isinstance(tool_calls, list):
        record.set_attribute(GRAPH_NODE_TOOL_CALLS, len(tool_calls))
    record.set_attribute(GRAPH_NODE_RETRY_COUNT, 0)


def build_graph(
    *,
    settings: Settings,
    gateway: LLMGateway,
    session: AsyncSession | None = None,
    prompts: PromptLibrary | None = None,
    registry: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    reranker: Reranker | None = None,
    graph_repository: KnowledgeGraphRepository | None = None,
    graph_search: GraphSearch | None = None,
    retrieve: RetrieveFn | None = None,
    progress_provider: ProgressProvider | None = None,
    metadata_provider: MetadataProvider | None = None,
    course_name_provider: CourseNameProvider | None = None,
    scope_check: ScopeCheck | None = None,
) -> CompiledStateGraph[ConversationState, None, ConversationState, ConversationState]:
    """Build and compile the graph.

    ``session`` is the caller's tenant-scoped session; every repository read and
    every tool handler inherits its tenancy. When it is absent the graph still
    compiles and runs — retrieval returns empty evidence and the composer refuses
    — which is what lets the routing and node unit tests run with no database.
    """
    active_registry = registry or build_tool_registry(settings, graph_repository=graph_repository)
    executor = ToolExecutor(active_registry, settings=settings, session=session, gateway=gateway)
    active_prompts = prompts or PromptLibrary(settings)
    metadata = metadata_provider or _document_metadata_provider(session)
    course_name = course_name_provider or _course_name_provider(session)
    scope = scope_check or _course_scope_check(session)

    builder: StateGraph[ConversationState] = StateGraph(ConversationState)
    langsmith_handle = langsmith.configure_langsmith(settings)
    config_version = settings.retrieval_config_version

    def add(name: str, node: NodeFn) -> None:
        """Register a node, wrapped in its per-node span and LangSmith run."""
        _add_node(
            builder,
            name,
            _observed_node(
                name, node, langsmith_handle=langsmith_handle, config_version=config_version
            ),
        )

    add("intent_router", make_intent_router_node(settings=settings, gateway=gateway))
    add(
        "student_context",
        make_student_context_node(settings=settings, progress_provider=progress_provider),
    )
    for role in ROLES.values():
        add(role.node_name, make_agent_node(role, settings=settings, gateway=gateway))
    add("plan_retrieval", make_plan_retrieval_node(settings=settings, scope_check=scope))
    add(
        "retrieval",
        make_retrieval_node(
            settings=settings, session=session, retrieve=retrieve, reranker=reranker
        ),
    )
    add("rerank", make_rerank_node(settings=settings, reranker=reranker))
    add(
        "knowledge_graph",
        make_knowledge_graph_node(
            settings=settings, repository=graph_repository, graph_search=graph_search
        ),
    )
    add("tools", make_tools_node(settings=settings, executor=executor))
    add(
        "answer_composer",
        make_answer_composer_node(
            settings=settings,
            gateway=gateway,
            prompts=active_prompts,
            metadata_provider=metadata,
            course_name_provider=course_name,
        ),
    )
    add("safety_guardrail", make_safety_guardrail_node(settings=settings))

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "student_context")
    builder.add_conditional_edges("student_context", route_intent, _INTENT_TARGETS)
    for role in ROLES.values():
        builder.add_conditional_edges(
            role.node_name,
            partial(route_after_agent, settings=settings),
            _AFTER_AGENT_TARGETS,
        )
    builder.add_edge("plan_retrieval", "retrieval")
    builder.add_edge("retrieval", "rerank")
    builder.add_edge("rerank", "knowledge_graph")
    builder.add_conditional_edges(
        "knowledge_graph",
        partial(route_after_evidence, settings=settings),
        _AFTER_EVIDENCE_TARGETS,
    )
    builder.add_conditional_edges(
        "tools",
        partial(route_after_agent, settings=settings),
        _AFTER_AGENT_TARGETS,
    )
    builder.add_edge(COMPOSE, "safety_guardrail")
    builder.add_edge("safety_guardrail", END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Default providers, backed by the caller's tenant-scoped session
# ---------------------------------------------------------------------------
def _document_metadata_provider(
    session: AsyncSession | None,
) -> MetadataProvider | None:
    if session is None:
        return None

    async def provider(state: ConversationState) -> dict[uuid.UUID, DocumentMeta]:
        scope = TenantScope(state["tenant_id"])
        repository = DocumentRepository(session, scope)
        result: dict[uuid.UUID, DocumentMeta] = {}
        for value in {
            document["document_id"] for document in state.get("retrieved_documents") or []
        }:
            document_id = _uuid(value)
            if document_id is None:
                continue
            document = await repository.get(document_id)
            if document is None:
                continue
            result[document_id] = DocumentMeta(
                document_id=document.id,
                filename=document.filename,
                source_type=document.source_type,
                content_type=document.content_type,
                page_count=document.page_count,
            )
        return result

    return provider


def _course_name_provider(session: AsyncSession | None) -> CourseNameProvider | None:
    if session is None:
        return None

    async def provider(state: ConversationState) -> str:
        course_id = state.get("current_course_id")
        if course_id is None:
            return "your course"
        course = await CourseRepository(session, TenantScope(state["tenant_id"])).get(course_id)
        return course.name if course is not None else "your course"

    return provider


def _course_scope_check(session: AsyncSession | None) -> ScopeCheck:
    """Tenant-ownership check for a model-suggested ``course_id``.

    Without a session the deterministic fallback applies: a course id is allowed
    only when it is the turn's already-scoped course. With a session, a course
    that exists in the caller's tenant is also allowed, because the repository
    cannot see another tenant's row.
    """

    async def check(state: ConversationState, course_id: uuid.UUID | None) -> bool:
        if course_id is None:
            return True
        if course_id == state.get("current_course_id"):
            return True
        if session is None:
            return False
        course = await CourseRepository(session, TenantScope(state["tenant_id"])).get(course_id)
        return course is not None

    return check


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


__all__ = ["build_graph"]
