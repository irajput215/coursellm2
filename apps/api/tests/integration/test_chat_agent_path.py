"""The HTTP chat path runs the agent graph, end to end against PostgreSQL.

Before this file existed the agent layer was implemented and unit-tested but
nothing on the request path invoked it: ``POST /api/v1/chat`` always ran the
direct retrieval-augmented flow. These tests assert the properties that only the
wiring can provide — a routed intent persisted on the assistant row, the tool
registry executed on the request's own tenant-scoped session, the permission
matrix enforced for a non-owning agent, a bound that degrades rather than raises,
one message row per role, and an ``agent_graph`` span with its node children.

Nothing here mocks ``litellm``: :class:`ScriptedGateway` subclasses the real
gateway and overrides only the provider call, so retry, validation, accounting
and response normalisation are the production paths. Retrieval, ingestion and
Row-Level Security are real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.api.app import create_app
from coursellm.api.deps import (
    ContextDep,
    SettingsDep,
    get_llm_gateway,
    get_settings_dep,
)
from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.db.models.identity import UserRole
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantContext, TenantScope, tenant_session
from coursellm.llm.gateway import LiteLLMGateway, _Completion, reset_gateway_cache
from coursellm.llm.types import LLMRequest, LLMScope, bind_llm_scope
from coursellm.observability import metrics, tracing
from coursellm.observability.attributes import SPAN_AGENT_GRAPH, SPAN_NODE_PREFIX
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.repositories.content import DocumentRepository
from coursellm.services import agent as agent_service
from coursellm.services import chat as chat_service

pytestmark = pytest.mark.integration

# Matches ``SEED_PASSWORD`` in the integration conftest.
SEED_PASSWORD = "seed-password-123"
DOCUMENT_FILENAME = "alpha-notes.txt"
QUESTION = "What do transformers rely on?"
CORPUS_MARKER = "self attention over the whole sequence"
DOCUMENT_TEXT = (
    b"Transformers rely on self attention over the whole sequence. "
    b"The attention mechanism mixes information across positions. "
    b"Feed forward layers follow every attention sublayer. "
    b"Residual connections stabilise deep transformer training. "
    b"Positional encodings tell the model about token order. "
)

# The scripts the graph consumes, in call order: router, agent decision, composer.
_ROUTER_TUTOR = json.dumps({"intent": "tutor", "confidence": 0.9, "reason": "question"})
_ROUTER_PLANNER = json.dumps({"intent": "planner", "confidence": 0.9, "reason": "plan"})
_ROUTER_PROGRESS = json.dumps({"intent": "progress", "confidence": 0.9, "reason": "progress"})
_TUTOR_RETRIEVE = json.dumps(
    {
        "action": "retrieve",
        "tool_calls": [{"name": "search_documents", "arguments": {"query": "transformers"}}],
        "answer": None,
        "rationale": "need evidence",
    }
)
_TUTOR_DENIED_TOOL = json.dumps(
    {
        "action": "tool",
        "tool_calls": [{"name": "create_quiz", "arguments": {"topic": "transformers"}}],
        "answer": None,
        "rationale": "the tutor may not create quizzes",
    }
)
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
_PROGRESS_TOOL = json.dumps(
    {
        "action": "tool",
        "tool_calls": [{"name": "get_student_progress", "arguments": {}}],
        "summary": None,
        "weak_concepts": [],
        "next_actions": [],
        "rationale": "refresh progress",
    }
)
_COMPOSER_TEXT = "Transformers rely on self attention over the whole sequence [S1]."

#: Attribute keys that would carry student content if a call site ever set them.
FORBIDDEN_ATTRIBUTE_KEYS = frozenset(
    {
        "prompt",
        "prompt_text",
        "messages",
        "content",
        "document_content",
        "document_text",
        "chunk_text",
        "question",
        "answer",
        "completion",
    }
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _completion(text: str) -> _Completion:
    return _Completion(
        text=text,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        finish_reason="stop",
        latency_ms=1.0,
    )


class ScriptedGateway(LiteLLMGateway):
    """The real gateway with only the provider call replaced by a script."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        *,
        texts: list[str],
    ) -> None:
        super().__init__(settings, session_factory, sleep=_no_sleep)
        self._texts = list(texts)

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
        return _completion(self._next_text())

    def _next_text(self) -> str:
        if not self._texts:
            raise AssertionError(
                "The scripted gateway received more calls than it was given texts."
            )
        return self._texts.pop(0)


class LoopingGateway(ScriptedGateway):
    """A gateway that always asks for another retrieval pass.

    Used to drive the turn into its iteration bound without a provider: the tutor
    agent keeps declaring ``search_documents`` and the composer never gets to
    ground an answer, so the only way the turn can end is a budget gate.
    """

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        super().__init__(settings, session_factory, texts=[])

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
        response_model = request.response_model
        if response_model is not None and response_model.__name__ == "IntentClassification":
            return _completion(_ROUTER_TUTOR)
        return _completion(_TUTOR_RETRIEVE)


class UnavailableAfterScriptGateway(ScriptedGateway):
    """A scripted gateway whose provider disappears after the routing calls."""

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
            raise ServiceUnavailableError("The provider is disabled for this turn.")
        return _completion(self._next_text())


@dataclass
class Script:
    """A mutable per-test script handed to the scripted gateway."""

    texts: list[str] = field(default_factory=list)


@dataclass
class Harness:
    client: AsyncClient
    settings: Settings
    seeded: Any
    script: Script


@pytest.fixture
def script() -> Script:
    return Script()


@pytest.fixture
def agent_settings(pg_settings: Settings) -> Settings:
    """RLS-enforcing settings with the gateway on and a pool wide enough for usage."""
    return pg_settings.model_copy(
        update={
            "llm_enabled": True,
            "fallback_model": "",
            "llm_max_retries": 0,
            "rerank_enabled": False,
            "db_pool_size": 5,
            "db_max_overflow": 0,
        }
    )


def _gateway_override(script: Script) -> Any:
    async def override(
        context: ContextDep, settings_dep: SettingsDep
    ) -> AsyncIterator[ScriptedGateway]:
        session_factory = await get_session_factory(settings_dep)
        gateway = ScriptedGateway(settings_dep, session_factory, texts=script.texts)
        scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
        with bind_llm_scope(scope):
            yield gateway

    return override


@pytest_asyncio.fixture(loop_scope="function")
async def chat(agent_settings: Settings, seeded: Any, script: Script) -> AsyncIterator[Harness]:
    application = create_app(agent_settings)
    application.dependency_overrides[get_settings_dep] = lambda: agent_settings
    application.dependency_overrides[get_llm_gateway] = _gateway_override(script)
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Harness(client=client, settings=agent_settings, seeded=seeded, script=script)
    finally:
        application.dependency_overrides.clear()
        reset_gateway_cache()


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ingest(settings: Settings, seeded: Any) -> None:
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        await ingest_document(
            session,
            settings,
            document=document,
            data=DOCUMENT_TEXT,
            embedder=HashingEmbedder(dim=EMBEDDING_DIM),
        )


def _context(seeded: Any) -> TenantContext:
    return TenantContext(
        tenant_id=seeded.tenant_a_id,
        user_id=seeded.user_a_id,
        role=UserRole.OWNER,
    )


async def _ask(
    case: Harness,
    *,
    question: str = QUESTION,
    engine: str = "agent",
    course_id: Any = None,
) -> dict[str, Any]:
    token = await _login(case.client, case.seeded.tenant_a.email)
    response = await case.client.post(
        "/api/v1/chat",
        json={
            "question": question,
            "course_id": str(course_id or case.seeded.tenant_a.course_id),
            "engine": engine,
        },
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _assistant_rows(owner_engine: AsyncEngine, conversation_id: Any) -> list[Any]:
    async with owner_engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT role, intent FROM messages WHERE conversation_id = :cid "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"cid": str(conversation_id)},
        )
        return list(result.mappings().all())


class TestTutorTurn:
    async def test_tutor_question_grounds_and_records_the_routed_intent(
        self,
        agent_settings: Settings,
        seeded: Any,
        chat: Harness,
        owner_engine: AsyncEngine,
    ) -> None:
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]

        body = await _ask(chat)

        assert body["intent"] == "tutor"
        assert body["grounded"] is True
        assert body["degraded"] == []
        assert body["citations"], body
        citation = body["citations"][0]
        assert citation["citation_id"] == "S1"
        assert citation["document_id"] == str(seeded.tenant_a.document_id)
        assert citation["filename"] == DOCUMENT_FILENAME

        rows = await _assistant_rows(owner_engine, body["conversation_id"])
        assert sorted(row["role"] for row in rows) == ["assistant", "user"]
        assistant = next(row for row in rows if row["role"] == "assistant")
        assert assistant["intent"] == "tutor"

    async def test_the_transcript_orders_the_question_before_the_answer(
        self,
        agent_settings: Settings,
        seeded: Any,
        chat: Harness,
        owner_engine: AsyncEngine,
    ) -> None:
        """Regression: a turn used to be able to render the answer first.

        Both messages are inserted in ONE transaction. PostgreSQL's ``now()``
        returns the transaction timestamp, not the statement's, so the server
        default gave the user and assistant rows identical ``created_at`` values.
        The transcript orders by ``(created_at, id)`` and ``id`` is a random UUID,
        so the order was a coin flip. ``run_turn`` now pins distinct timestamps.
        """
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]

        body = await _ask(chat)

        async with owner_engine.connect() as connection:
            ordered = (
                (
                    await connection.execute(
                        text(
                            "SELECT role FROM messages WHERE conversation_id = :cid "
                            "ORDER BY created_at, id"
                        ),
                        {"cid": body["conversation_id"]},
                    )
                )
                .scalars()
                .all()
            )

        assert list(ordered) == ["user", "assistant"], (
            f"transcript order is not deterministic: {list(ordered)}"
        )

    async def test_the_trace_id_field_is_present_and_null_when_tracing_is_off(
        self, agent_settings: Settings, seeded: Any, chat: Harness
    ) -> None:
        # Tracing is off in this fixture, so the response carries no trace id;
        # the traced hierarchy is asserted separately below.
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]
        body = await _ask(chat)
        assert "trace_id" in body
        assert body["trace_id"] is None


class TestPlannerTurn:
    async def test_planner_intent_routes_to_the_planner_and_builds_a_roadmap(
        self,
        agent_settings: Settings,
        seeded: Any,
        chat: Harness,
        owner_engine: AsyncEngine,
    ) -> None:
        # The planner was unreachable from HTTP before this wiring: no router
        # called the graph, so no roadmap could be produced for a request.
        chat.script.texts = [_ROUTER_PLANNER, _PLANNER_ANSWER]

        body = await _ask(chat, question="Build me a roadmap to learn transformers.")

        assert body["intent"] == "planner"
        assert "Attention" in body["answer"]
        assert "Transformers" in body["answer"]
        rows = await _assistant_rows(owner_engine, body["conversation_id"])
        assistant = next(row for row in rows if row["role"] == "assistant")
        assert assistant["intent"] == "planner"


class TestEngineEquivalence:
    async def test_both_engines_return_the_same_citation_ids_for_one_question(
        self, agent_settings: Settings, seeded: Any
    ) -> None:
        await _ingest(agent_settings, seeded)
        context = _context(seeded)
        scope = TenantScope(seeded.tenant_a_id)

        async with tenant_session(agent_settings, scope) as session:
            agent_gateway = ScriptedGateway(
                agent_settings, None, texts=[_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]
            )
            agent_answer = await chat_service.ask(
                session,
                agent_settings,
                agent_gateway,
                context,
                question=QUESTION,
                course_id=seeded.tenant_a.course_id,
                conversation_id=None,
                engine="agent",
            )

        async with tenant_session(agent_settings, scope) as session:
            rag_gateway = ScriptedGateway(agent_settings, None, texts=[_COMPOSER_TEXT])
            rag_answer = await chat_service.ask(
                session,
                agent_settings,
                rag_gateway,
                context,
                question=QUESTION,
                course_id=seeded.tenant_a.course_id,
                conversation_id=None,
                engine="rag",
            )

        assert agent_answer.grounded is True
        assert rag_answer.grounded is True
        assert [citation.citation_id for citation in agent_answer.citations] == [
            citation.citation_id for citation in rag_answer.citations
        ]
        assert [citation.document_id for citation in agent_answer.citations] == [
            citation.document_id for citation in rag_answer.citations
        ]
        assert [citation.filename for citation in agent_answer.citations] == [
            citation.filename for citation in rag_answer.citations
        ]

    async def test_the_rag_engine_remains_reachable_with_the_explicit_opt_out(
        self, agent_settings: Settings, seeded: Any, chat: Harness
    ) -> None:
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_COMPOSER_TEXT]

        body = await _ask(chat, engine="rag")

        assert body["intent"] == "tutor"
        assert body["grounded"] is True
        assert body["citations"][0]["document_id"] == str(seeded.tenant_a.document_id)


class TestToolsOnTheRequestPath:
    async def test_a_progress_question_executes_get_student_progress(
        self, agent_settings: Settings, seeded: Any
    ) -> None:
        context = _context(seeded)
        scope = TenantScope(seeded.tenant_a_id)
        async with tenant_session(agent_settings, scope) as session:
            gateway = ScriptedGateway(
                agent_settings, None, texts=[_ROUTER_PROGRESS, _PROGRESS_TOOL]
            )
            answer = await chat_service.ask(
                session,
                agent_settings,
                gateway,
                context,
                question="What is my progress in this course?",
                course_id=seeded.tenant_a.course_id,
                conversation_id=None,
                engine="agent",
            )

        assert answer.intent == "progress"
        progress_calls = [
            record for record in answer.tool_calls if record["tool"] == "get_student_progress"
        ]
        assert progress_calls, answer.tool_calls
        assert progress_calls[0]["status"] == "ok"
        assert answer.answer

    async def test_a_denied_tool_call_is_recorded_and_has_no_side_effect(
        self, agent_settings: Settings, seeded: Any
    ) -> None:
        # ``create_quiz`` is not on the tutor's allowlist. The call must be
        # recorded as denied and must never reach the handler.
        context = _context(seeded)
        scope = TenantScope(seeded.tenant_a_id)
        async with tenant_session(agent_settings, scope) as session:
            gateway = ScriptedGateway(
                agent_settings,
                None,
                texts=[_ROUTER_TUTOR, _TUTOR_DENIED_TOOL, _COMPOSER_TEXT],
            )
            turn = await agent_service.run_turn(
                session,
                agent_settings,
                gateway,
                context,
                question=QUESTION,
                course_id=seeded.tenant_a.course_id,
            )

        denied = [record for record in turn.tool_calls if record["status"] == "denied"]
        assert denied, turn.tool_calls
        assert denied[0]["tool"] == "create_quiz"
        # No side effect: the denied call never produced a quiz artifact.
        assert turn.quiz is None


class TestBounds:
    async def test_a_forced_iteration_bound_returns_a_well_formed_turn(
        self,
        agent_settings: Settings,
        seeded: Any,
    ) -> None:
        bounded = agent_settings.model_copy(
            update={"graph_max_steps": 2, "agent_max_tool_calls_per_turn": 64}
        )
        application = create_app(bounded)
        application.dependency_overrides[get_settings_dep] = lambda: bounded

        async def override(
            context: ContextDep, settings_dep: SettingsDep
        ) -> AsyncIterator[LoopingGateway]:
            session_factory = await get_session_factory(settings_dep)
            gateway = LoopingGateway(settings_dep, session_factory)
            scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
            with bind_llm_scope(scope):
                yield gateway

        application.dependency_overrides[get_llm_gateway] = override
        transport = ASGITransport(app=application)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                token = await _login(client, seeded.tenant_a.email)
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "question": QUESTION,
                        "course_id": str(seeded.tenant_a.course_id),
                    },
                    headers=_auth(token),
                )
        finally:
            application.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["degraded"], body
        assert "iteration_limit_reached" in body["degraded"]
        assert body["answer"]


class TestPersistence:
    async def test_each_request_writes_exactly_one_user_and_one_assistant_row(
        self,
        agent_settings: Settings,
        seeded: Any,
        chat: Harness,
        owner_engine: AsyncEngine,
    ) -> None:
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]

        body = await _ask(chat)

        rows = await _assistant_rows(owner_engine, body["conversation_id"])
        roles = [row["role"] for row in rows]
        assert roles.count("user") == 1, roles
        assert roles.count("assistant") == 1, roles

    async def test_the_direct_engine_also_writes_exactly_two_rows(
        self,
        agent_settings: Settings,
        seeded: Any,
        chat: Harness,
        owner_engine: AsyncEngine,
    ) -> None:
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_COMPOSER_TEXT]

        body = await _ask(chat, engine="rag")

        rows = await _assistant_rows(owner_engine, body["conversation_id"])
        assert [row["role"] for row in rows] == ["user", "assistant"]


class TestStreaming:
    async def test_the_agent_engine_stream_preserves_the_sse_contract(
        self, agent_settings: Settings, seeded: Any, chat: Harness
    ) -> None:
        await _ingest(agent_settings, seeded)
        chat.script.texts = [_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT]
        token = await _login(chat.client, seeded.tenant_a.email)

        response = await chat.client.post(
            "/api/v1/chat/stream",
            json={
                "question": QUESTION,
                "course_id": str(seeded.tenant_a.course_id),
            },
            headers=_auth(token),
        )

        assert response.status_code == 200, response.text
        body = response.text
        assert body.index("event: token") < body.index("event: citations")
        assert body.index("event: citations") < body.index("event: done")
        assert CORPUS_MARKER in body
        assert str(seeded.tenant_a.document_id) in body


class TestObservability:
    async def test_a_chat_request_emits_the_agent_graph_with_node_children(
        self, agent_settings: Settings, seeded: Any
    ) -> None:
        tracing.reset_tracing()
        exporter = InMemorySpanExporter()
        tracing.set_test_span_exporter(exporter)
        metrics.reset_registry()
        settings = agent_settings.model_copy(
            update={
                "otel_enabled": True,
                "otel_traces_exporter": "otlp",
                "otel_sample_ratio": 1.0,
                "metrics_enabled": True,
                "langsmith_enabled": False,
            }
        )
        await _ingest(settings, seeded)
        script = Script(texts=[_ROUTER_TUTOR, _TUTOR_RETRIEVE, _COMPOSER_TEXT])
        application = create_app(settings)
        application.dependency_overrides[get_settings_dep] = lambda: settings
        application.dependency_overrides[get_llm_gateway] = _gateway_override(script)
        transport = ASGITransport(app=application)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                token = await _login(client, seeded.tenant_a.email)
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "question": QUESTION,
                        "course_id": str(seeded.tenant_a.course_id),
                    },
                    headers=_auth(token),
                )
                assert response.status_code == 200, response.text
                body = response.json()
        finally:
            handle = getattr(application.state, "tracing_handle", None)
            if handle is not None and handle.provider is not None:
                handle.provider.force_flush()
            application.dependency_overrides.clear()
            tracing.shutdown_tracing(handle)
            tracing.reset_tracing()
            tracing.set_test_span_exporter(None)

        spans = list(exporter.get_finished_spans())
        assert spans, "no spans were exported"
        by_id = {span.context.span_id: span for span in spans}
        by_name: dict[str, list[Any]] = {}
        for span in spans:
            by_name.setdefault(span.name, []).append(span)

        graph_spans = by_name.get(SPAN_AGENT_GRAPH)
        assert graph_spans, f"no agent_graph span; saw {sorted(by_name)}"
        graph = graph_spans[0]

        children = {
            span.name
            for span in spans
            if span.parent is not None and span.parent.span_id == graph.context.span_id
        }
        for node in (
            "intent_router",
            "student_context",
            "tutor_agent",
            "answer_composer",
        ):
            assert f"{SPAN_NODE_PREFIX}:{node}" in children, sorted(children)

        generation = by_name.get("generation")
        assert generation, sorted(by_name)
        generation_parent = generation[0].parent
        assert generation_parent is not None
        assert generation_parent.span_id in by_id
        assert by_id[generation_parent.span_id].name.startswith(SPAN_NODE_PREFIX)

        trace_ids = {format(span.context.trace_id, "032x") for span in spans}
        assert body["trace_id"] in trace_ids

        for span in spans:
            attributes = span.attributes or {}
            for key in attributes:
                assert key not in FORBIDDEN_ATTRIBUTE_KEYS, f"{span.name} carries {key!r}"
            for value in attributes.values():
                for text_value in _strings(value):
                    assert CORPUS_MARKER not in text_value, f"{span.name} leaked document text"
                    assert QUESTION not in text_value, f"{span.name} leaked the question"
                    assert _COMPOSER_TEXT not in text_value, f"{span.name} leaked the completion"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


class TestDegradation:
    async def test_llm_disabled_still_answers_through_the_graph(
        self, pg_settings: Settings, seeded: Any
    ) -> None:
        settings = pg_settings.model_copy(
            update={
                "llm_enabled": False,
                "fallback_model": "",
                "llm_max_retries": 0,
                "rerank_enabled": False,
                "db_pool_size": 5,
                "db_max_overflow": 0,
            }
        )
        await _ingest(settings, seeded)
        reset_gateway_cache()
        application = create_app(settings)
        application.dependency_overrides[get_settings_dep] = lambda: settings
        transport = ASGITransport(app=application)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                token = await _login(client, seeded.tenant_a.email)
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "question": QUESTION,
                        "course_id": str(seeded.tenant_a.course_id),
                    },
                    headers=_auth(token),
                )
        finally:
            application.dependency_overrides.clear()
            reset_gateway_cache()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intent"] == "tutor"
        assert body["grounded"] is False
        assert body["degraded"], body
        assert body["answer"]

    async def test_a_provider_failure_degrades_to_the_extractive_answer(
        self, agent_settings: Settings, seeded: Any
    ) -> None:
        await _ingest(agent_settings, seeded)
        script = Script(texts=[_ROUTER_TUTOR, _TUTOR_RETRIEVE])
        application = create_app(agent_settings)
        application.dependency_overrides[get_settings_dep] = lambda: agent_settings

        async def override(
            context: ContextDep, settings_dep: SettingsDep
        ) -> AsyncIterator[UnavailableAfterScriptGateway]:
            session_factory = await get_session_factory(settings_dep)
            gateway = UnavailableAfterScriptGateway(
                settings_dep, session_factory, texts=script.texts
            )
            scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
            with bind_llm_scope(scope):
                yield gateway

        application.dependency_overrides[get_llm_gateway] = override
        transport = ASGITransport(app=application)
        try:
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                token = await _login(client, seeded.tenant_a.email)
                response = await client.post(
                    "/api/v1/chat",
                    json={
                        "question": QUESTION,
                        "course_id": str(seeded.tenant_a.course_id),
                    },
                    headers=_auth(token),
                )
        finally:
            application.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        body = response.json()
        assert CORPUS_MARKER in body["answer"], body
        assert "llm_unavailable" in body["degraded"], body
