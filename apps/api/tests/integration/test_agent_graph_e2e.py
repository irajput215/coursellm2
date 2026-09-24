"""End-to-end graph runs against real PostgreSQL, with a scripted model.

Nothing here mocks ``litellm``: :class:`ScriptedGateway` subclasses the real
:class:`~coursellm.llm.gateway.LiteLLMGateway` and overrides only the provider
call, so routing, fallback and response normalisation are the production paths.
Retrieval is real: documents are seeded with the ingestion pipeline so pgvector,
BM25 and Row-Level Security are all exercised.

The assertions are on typed state — intent, citations, degraded reasons, tool
statuses and persisted rows — never on prose.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.agents.graph import build_graph
from coursellm.agents.state import ConversationState, initial_state
from coursellm.core.config import Settings
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.db.models.identity import UserRole
from coursellm.db.tenancy import TenantContext, TenantScope, tenant_session
from coursellm.llm.gateway import LiteLLMGateway, _Completion
from coursellm.llm.types import LLMRequest
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.repositories.content import DocumentRepository
from coursellm.services import agent as agent_service
from coursellm.tools import build_tool_registry
from coursellm.tools.registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.integration

QUESTION = "What do transformers rely on?"
DOCUMENT_TEXT = (
    b"Transformers rely on self attention over the whole sequence. "
    b"The attention mechanism mixes information across positions. "
    b"Feed forward layers follow every attention sublayer. "
)

_ROUTER_TUTOR = json.dumps({"intent": "tutor", "confidence": 0.9, "reason": "question"})
_ROUTER_PLANNER = json.dumps({"intent": "planner", "confidence": 0.9, "reason": "plan"})
_TUTOR_RETRIEVE = json.dumps(
    {
        "action": "retrieve",
        "tool_calls": [{"name": "search_documents", "arguments": {"query": "transformers"}}],
        "answer": None,
        "rationale": "need evidence",
    }
)
_TUTOR_TOOL = json.dumps(
    {
        "action": "tool",
        "tool_calls": [{"name": "get_student_progress", "arguments": {}}],
        "answer": None,
        "rationale": "check progress",
    }
)
_TUTOR_ANSWER = json.dumps(
    {
        "action": "answer",
        "tool_calls": [],
        "answer": "Transformers rely on self attention [S1].",
        "rationale": "answer from evidence",
    }
)
#: Plain prose, for the composer's generation call (no response schema).
_COMPOSER_TEXT = "Transformers rely on self attention over the whole sequence [S1]."
_PLANNER_ANSWER = json.dumps(
    {
        "action": "answer",
        "tool_calls": [],
        "summary": "Start with attention, then transformers.",
        "steps": [
            {"concept_id": "attention", "title": "Attention", "order": 0},
            {"concept_id": "transformers", "title": "Transformers", "order": 1},
        ],
        "rationale": "ordering",
    }
)


async def _no_sleep(_seconds: float) -> None:
    return None


class ScriptedGateway(LiteLLMGateway):
    """The real gateway with only the provider call replaced by a script.

    ``session_factory`` is ``None`` and no ambient scope is bound, so accounting
    is skipped: these tests assert graph behaviour, and the usage rows themselves
    are covered by the chat-flow suite.
    """

    def __init__(self, settings: Settings, *, texts: list[str]) -> None:
        super().__init__(settings, None, sleep=_no_sleep)
        self._texts = list(texts)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return await super().complete(request)

    async def _invoke(
        self,
        model: str,
        request: LLMRequest,
        *,
        attempt: int,
        provider: str,
        used_fallback: bool,
        repair: str | None = None,
    ) -> _Completion:
        if not self._texts:
            raise AssertionError("The scripted gateway received more calls than it was given.")
        return _Completion(
            text=self._texts.pop(0),
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
            latency_ms=1.0,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        yield ""


@pytest.fixture
def agent_settings(pg_settings: Settings) -> Settings:
    """RLS-enforcing settings with the gateway enabled and no fallback model."""
    return pg_settings.model_copy(
        update={"llm_enabled": True, "fallback_model": "", "llm_max_retries": 0}
    )


def _state(*, course_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID) -> ConversationState:
    return initial_state(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        deadline_ns=time.monotonic_ns() + 60_000_000_000,
        messages=[HumanMessage(content=QUESTION)],
        current_course_id=course_id,
    )


def _config(thread_id: uuid.UUID, tenant_id: uuid.UUID, settings: Settings) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": agent_service.checkpoint_thread_id(tenant_id, thread_id),
        },
        "recursion_limit": settings.graph_recursion_limit,
    }


async def _ingest(settings: Settings, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
    scope = TenantScope(tenant_id)
    async with tenant_session(settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(document_id)
        await ingest_document(
            session,
            settings,
            document=document,
            data=DOCUMENT_TEXT,
            embedder=HashingEmbedder(dim=EMBEDDING_DIM),
        )


async def _message_count(owner_engine: AsyncEngine, conversation_id: uuid.UUID) -> int:
    async with owner_engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM messages WHERE conversation_id = :cid"),
                    {"cid": str(conversation_id)},
                )
            ).scalar_one()
        )


class TestTutorTurn:
    async def test_a_tutor_turn_retrieves_and_cites(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        await _ingest(agent_settings, seeded.tenant_a_id, seeded.tenant_a.document_id)
        gateway = ScriptedGateway(
            agent_settings, texts=[_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]
        )
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(agent_settings, scope) as session:
            graph = build_graph(
                settings=agent_settings,
                gateway=gateway,
                session=session,
                checkpointer=InMemorySaver(),
            )
            state = _state(
                course_id=seeded.tenant_a.course_id,
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
            )
            result = await graph.ainvoke(
                state, _config(state["conversation_id"], seeded.tenant_a_id, agent_settings)
            )

        assert result["intent"] == "tutor"
        assert result["grounding"]["grounded"] is True
        assert [reason.value for reason in result["degraded"]] == []
        assert result["citations"]
        assert result["citations"][0]["document_id"] == str(seeded.tenant_a.document_id)
        assert "S1" in result["answer_draft"]["text"]
        assert [decision["node"] for decision in result["agent_decisions"]] == [
            "intent_router",
            "tutor_agent",
            "plan_retrieval",
        ]


class TestPlannerTurn:
    async def test_a_planner_turn_produces_a_roadmap(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        gateway = ScriptedGateway(agent_settings, texts=[_ROUTER_PLANNER, _PLANNER_ANSWER])
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(agent_settings, scope) as session:
            graph = build_graph(
                settings=agent_settings,
                gateway=gateway,
                session=session,
                checkpointer=InMemorySaver(),
            )
            state = _state(
                course_id=seeded.tenant_a.course_id,
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
            )
            result = await graph.ainvoke(
                state, _config(state["conversation_id"], seeded.tenant_a_id, agent_settings)
            )

        assert result["intent"] == "planner"
        assert result["roadmap"] is not None
        assert [step["title"] for step in result["roadmap"]["steps"]] == [
            "Attention",
            "Transformers",
        ]
        assert "Attention" in result["answer_draft"]["text"]


class TestTenantIsolation:
    async def test_a_tutor_turn_cannot_see_another_tenants_documents(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        # Only tenant B's corpus is ingested. Tenant A has a document row but no
        # chunks, and must not be able to retrieve tenant B's evidence.
        await _ingest(agent_settings, seeded.tenant_b_id, seeded.tenant_b.document_id)
        bounded = agent_settings.model_copy(update={"agent_max_retrieval_passes": 1})
        gateway = ScriptedGateway(bounded, texts=[_ROUTER_TUTOR, _TUTOR_RETRIEVE])
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(bounded, scope) as session:
            graph = build_graph(
                settings=bounded,
                gateway=gateway,
                session=session,
                checkpointer=InMemorySaver(),
            )
            state = _state(
                course_id=seeded.tenant_a.course_id,
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
            )
            result = await graph.ainvoke(
                state, _config(state["conversation_id"], seeded.tenant_a_id, bounded)
            )

        assert result["retrieved_documents"] == []
        assert result["citations"] == []
        assert result["grounding"]["grounded"] is False
        assert str(seeded.tenant_b.document_id) not in json.dumps(
            [dict(document) for document in result["retrieved_documents"]]
        )
        assert "don't have enough information" in result["answer_draft"]["text"]


class TestToolFailure:
    async def test_a_tool_failure_mid_turn_still_yields_an_answer(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        registry = _registry_with_failing_progress(agent_settings)
        gateway = ScriptedGateway(agent_settings, texts=[_ROUTER_TUTOR, _TUTOR_TOOL])
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(agent_settings, scope) as session:
            graph = build_graph(
                settings=agent_settings,
                gateway=gateway,
                session=session,
                registry=registry,
                checkpointer=InMemorySaver(),
            )
            state = _state(
                course_id=seeded.tenant_a.course_id,
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
            )
            result = await graph.ainvoke(
                state, _config(state["conversation_id"], seeded.tenant_a_id, agent_settings)
            )

        statuses = [record["status"] for record in result["tool_calls"]]
        assert "error" in statuses
        assert "tool_error" in {reason.value for reason in result["degraded"]}
        assert result["answer_draft"]["text"]


class TestPersistence:
    async def test_a_completed_turn_is_persisted(
        self,
        agent_settings: Settings,
        seeded: Any,
        owner_engine: AsyncEngine,
    ) -> None:
        gateway = ScriptedGateway(agent_settings, texts=[_ROUTER_TUTOR, _TUTOR_ANSWER])
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(agent_settings, scope) as session:
            graph = build_graph(
                settings=agent_settings,
                gateway=gateway,
                session=session,
                checkpointer=InMemorySaver(),
            )
            context = TenantContext(
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
                role=UserRole.OWNER,
            )
            turn = await agent_service.run_turn(
                session,
                agent_settings,
                gateway,
                context,
                question=QUESTION,
                course_id=seeded.tenant_a.course_id,
                graph=graph,
            )
            conversation_id = turn.conversation_id

        assert turn.intent == "tutor"
        assert turn.answer
        assert await _message_count(owner_engine, conversation_id) == 2

    async def test_the_turn_is_checkpointed_under_the_tenant_namespace(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        gateway = ScriptedGateway(agent_settings, texts=[_ROUTER_TUTOR, _TUTOR_ANSWER])
        scope = TenantScope(seeded.tenant_a_id)
        saver = InMemorySaver()

        async with tenant_session(agent_settings, scope) as session:
            graph = build_graph(
                settings=agent_settings,
                gateway=gateway,
                session=session,
                checkpointer=saver,
            )
            state = _state(
                course_id=seeded.tenant_a.course_id,
                tenant_id=seeded.tenant_a_id,
                user_id=seeded.user_a_id,
            )
            config = _config(state["conversation_id"], seeded.tenant_a_id, agent_settings)
            await graph.ainvoke(state, config)

        # The checkpointer is not the product interface; the property under test is
        # that the thread is keyed by tenant *and* conversation, so two tenants
        # sharing a conversation id can never read each other's checkpoints.
        assert saver.storage
        assert all(str(key).startswith(f"{seeded.tenant_a_id}:") for key in saver.storage)


def _registry_with_failing_progress(settings: Settings) -> ToolRegistry:
    """The real registry with one tool's handler replaced by a failing one."""
    real = build_tool_registry(settings)
    registry = ToolRegistry()

    async def failing_progress(args: Any, ctx: Any) -> Any:
        raise RuntimeError("progress store unavailable")

    for spec in real.specs():
        registry.register(
            ToolSpec(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                required_permissions=spec.required_permissions,
                side_effects=spec.side_effects,
                timeout_ms=spec.timeout_ms,
                handler=failing_progress if spec.name == "get_student_progress" else spec.handler,
            )
        )
    return registry
