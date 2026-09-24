"""Integration tests for spans, metrics and the message trace columns.

Tracing is exercised with an :class:`InMemorySpanExporter`, so nothing in this
module opens a socket or depends on a collector. The request path is the real
one — scripted provider call, real retry/validation/usage code, real PostgreSQL —
so a span hierarchy assertion is a statement about production wiring rather than
about a hand-built tree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.llm.gateway import LiteLLMGateway, _Completion
from coursellm.llm.types import LLMRequest, LLMScope, bind_llm_scope
from coursellm.observability import metrics, tracing
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.rag.rerank.rerankers import LexicalReranker
from coursellm.repositories.content import DocumentRepository

pytestmark = pytest.mark.integration

SEED_PASSWORD = "seed-password-123"
QUESTION = "What do transformers rely on?"
CORPUS_MARKER = "self attention over the whole sequence"
ANSWER_TEXT = "Transformers rely on self attention over the whole sequence. [S1]"
DOCUMENT_TEXT = (
    b"Transformers rely on self attention over the whole sequence. "
    b"The attention mechanism mixes information across positions. "
    b"Feed forward layers follow every attention sublayer. "
    b"Residual connections stabilise deep transformer training. "
    b"Positional encodings tell the model about token order. "
)

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


class ScriptedGateway(LiteLLMGateway):
    """The real gateway with only the provider call replaced by a script.

    Retry, validation, accounting and the instrumentation added by the base
    class are inherited unchanged, so the LLM span under test is the production
    one.
    """

    def __init__(self, settings: Settings, session_factory: Any, *, texts: list[str]) -> None:
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
        text = self._texts.pop(0) if self._texts else ""
        return _Completion(
            text=text,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
            latency_ms=1.0,
        )


@dataclass
class TracedChat:
    """Everything a span assertion needs, in one object."""

    client: AsyncClient
    settings: Settings
    app: Any
    exporter: InMemorySpanExporter
    seeded: Any


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


def _app_with_gateway(settings: Settings, texts: list[str]) -> Any:
    application = create_app(settings)
    application.dependency_overrides[get_settings_dep] = lambda: settings

    async def _gateway_override(
        context: ContextDep, settings_dep: SettingsDep
    ) -> AsyncIterator[ScriptedGateway]:
        session_factory = await get_session_factory(settings_dep)
        gateway = ScriptedGateway(settings_dep, session_factory, texts=list(texts))
        scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
        with bind_llm_scope(scope):
            yield gateway

    application.dependency_overrides[get_llm_gateway] = _gateway_override
    return application


def _enable_tracing(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "llm_enabled": True,
            "fallback_model": "",
            "llm_max_retries": 0,
            "rerank_enabled": True,
            "db_pool_size": 5,
            "db_max_overflow": 0,
            "otel_enabled": True,
            "otel_traces_exporter": "otlp",
            "otel_sample_ratio": 1.0,
            "metrics_enabled": True,
            "langsmith_enabled": False,
        }
    )


@pytest_asyncio.fixture(loop_scope="function")
async def traced_chat(
    pg_settings: Settings,
    seeded: Any,
    exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[TracedChat]:
    settings = _enable_tracing(pg_settings)
    # A deterministic, dependency-free reranker: the cross-encoder would try to
    # download a model, which is neither what this test measures nor something a
    # test may depend on the network for.
    monkeypatch.setattr(
        "coursellm.rag.rerank.pipeline.get_reranker",
        lambda _settings: LexicalReranker(),
    )
    # Ingestion runs before tracing is configured so its SQL does not pollute
    # the trace under assertion.
    await _ingest(settings, seeded)
    application = _app_with_gateway(settings, [ANSWER_TEXT])
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield TracedChat(
                client=client,
                settings=settings,
                app=application,
                exporter=exporter,
                seeded=seeded,
            )
    finally:
        application.dependency_overrides.clear()
        tracing.shutdown_tracing(getattr(application.state, "tracing_handle", None))
        tracing.reset_tracing()
        tracing.set_test_span_exporter(None)


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    tracing.reset_tracing()
    memory = InMemorySpanExporter()
    tracing.set_test_span_exporter(memory)
    metrics.reset_registry()
    return memory


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _ask(case: TracedChat) -> dict[str, Any]:
    token = await _login(case.client, case.seeded.tenant_a.email)
    response = await case.client.post(
        "/api/v1/chat",
        json={"question": QUESTION, "course_id": str(case.seeded.tenant_a.course_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _force_flush(case: TracedChat) -> None:
    handle = getattr(case.app.state, "tracing_handle", None)
    assert handle is not None, "tracing was not installed"
    assert handle.provider is not None, "tracing has no provider"
    handle.provider.force_flush()


def _parent_name(span: Any, by_id: dict[int, Any]) -> str | None:
    parent = span.parent
    if parent is None or parent.span_id not in by_id:
        return None
    return str(by_id[parent.span_id].name)


class TestSpanHierarchy:
    async def test_chat_request_emits_the_documented_span_tree(
        self, traced_chat: TracedChat
    ) -> None:
        await _ask(traced_chat)
        _force_flush(traced_chat)
        spans = list(traced_chat.exporter.get_finished_spans())
        assert spans, "no spans were exported"

        by_id = {span.context.span_id: span for span in spans}
        by_name: dict[str, list[Any]] = {}
        for span in spans:
            by_name.setdefault(span.name, []).append(span)

        http = next((span for span in spans if span.name.endswith("/api/v1/chat")), None)
        assert http is not None, f"no HTTP span among {sorted(by_name)}"
        assert http.parent is None, "the server span must be the trace root"

        for name in (
            "retrieval",
            "retrieval.semantic",
            "retrieval.lexical",
            "retrieval.fusion",
            "retrieval.rerank",
            "generation",
            "llm_call",
        ):
            assert name in by_name, f"missing span {name!r}; saw {sorted(by_name)}"

        semantic = by_name["retrieval.semantic"][0]
        lexical = by_name["retrieval.lexical"][0]
        fusion = by_name["retrieval.fusion"][0]
        rerank = by_name["retrieval.rerank"][0]
        generation = by_name["generation"][0]
        llm_call = by_name["llm_call"][0]

        # The retrieval stages hang off a `retrieval` parent.
        assert _parent_name(semantic, by_id) == "retrieval"
        assert _parent_name(lexical, by_id) == "retrieval"
        assert _parent_name(fusion, by_id) == "retrieval"
        assert _parent_name(rerank, by_id) == "retrieval"
        # Generation is a child of the request, and the LLM call is inside it.
        assert _parent_name(generation, by_id) == http.name
        assert _parent_name(llm_call, by_id) == "generation"
        # One trace: every chat span shares the request's trace id. (The
        # exporter also holds the login request's spans, which are a separate
        # trace.)
        chat_trace_id = http.context.trace_id
        for span in (semantic, lexical, fusion, rerank, generation, llm_call):
            assert span.context.trace_id == chat_trace_id

    async def test_span_attributes_include_config_version_and_layer_latencies(
        self, traced_chat: TracedChat
    ) -> None:
        await _ask(traced_chat)
        _force_flush(traced_chat)
        spans = list(traced_chat.exporter.get_finished_spans())

        versions = {
            span.attributes.get("coursellm.config_version")
            for span in spans
            if span.attributes is not None
        }
        assert traced_chat.settings.retrieval_config_version in versions

        retrieval_spans = [span for span in spans if str(span.name).startswith("retrieval")]
        assert retrieval_spans
        assert any("retrieval.duration_ms" in (span.attributes or {}) for span in retrieval_spans)
        for span in retrieval_spans:
            if span.name in {
                "retrieval.semantic",
                "retrieval.lexical",
                "retrieval.fusion",
                "retrieval.rerank",
            }:
                assert "retrieval.duration_ms" in (span.attributes or {}), span.name

    async def test_no_span_carries_prompt_document_or_completion_text(
        self, traced_chat: TracedChat
    ) -> None:
        await _ask(traced_chat)
        _force_flush(traced_chat)
        spans = list(traced_chat.exporter.get_finished_spans())
        assert spans

        for span in spans:
            attributes = span.attributes or {}
            for key in attributes:
                assert key not in FORBIDDEN_ATTRIBUTE_KEYS, f"{span.name} carries {key!r}"
            for value in attributes.values():
                for text_value in _strings(value):
                    assert CORPUS_MARKER not in text_value, f"{span.name} leaked document text"
                    assert QUESTION not in text_value, f"{span.name} leaked the question"
                    assert ANSWER_TEXT not in text_value, f"{span.name} leaked the completion"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


class TestMetricsEndpoint:
    async def test_metrics_endpoint_is_public_and_leaks_no_tenant_identifier(
        self, traced_chat: TracedChat
    ) -> None:
        await _ask(traced_chat)
        response = await traced_chat.client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text

        for name in (
            "http.server.request.duration",
            "coursellm.requests.total",
            "coursellm.errors.total",
            "coursellm.retrieval.duration",
            "coursellm.rerank.duration",
            "coursellm.llm.duration",
            "coursellm.request.duration",
            "coursellm.llm.tokens",
            "coursellm.llm.cost_usd",
            "coursellm.llm.fallback.total",
            "coursellm.degraded.total",
            "coursellm.retrieval.candidates",
            "coursellm.rerank.score",
            "coursellm.eval.score",
            "coursellm.cache.hit_ratio",
            "coursellm.graph.runs.active",
        ):
            assert f"# TYPE {name} " in body, f"missing metric {name!r}"

        assert str(traced_chat.seeded.tenant_a_id) not in body
        assert str(traced_chat.seeded.user_a_id) not in body


class TestMessageTraceColumns:
    async def test_stored_assistant_message_joins_to_a_span(
        self, traced_chat: TracedChat, owner_engine: AsyncEngine
    ) -> None:
        body = await _ask(traced_chat)
        _force_flush(traced_chat)
        spans = list(traced_chat.exporter.get_finished_spans())

        async with owner_engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT intent, model, prompt_version, latency_ms, trace_id "
                            "FROM messages WHERE conversation_id = :conversation_id "
                            "AND role = 'assistant'"
                        ),
                        {"conversation_id": body["conversation_id"]},
                    )
                )
                .mappings()
                .one()
            )

        assert row["intent"] == "tutor"
        assert row["model"]
        assert row["prompt_version"]
        assert row["latency_ms"] is not None
        assert row["latency_ms"] >= 0
        assert row["trace_id"], "the assistant message has no trace id"
        trace_ids = {format(span.context.trace_id, "032x") for span in spans}
        assert row["trace_id"] in trace_ids


class TestTracingDisabled:
    async def test_request_succeeds_and_emits_no_spans(
        self, pg_settings: Settings, seeded: Any
    ) -> None:
        tracing.reset_tracing()
        memory = InMemorySpanExporter()
        tracing.set_test_span_exporter(memory)
        metrics.reset_registry()
        settings = pg_settings.model_copy(
            update={"llm_enabled": True, "fallback_model": "", "llm_max_retries": 0}
        )
        await _ingest(settings, seeded)
        application = _app_with_gateway(settings, [ANSWER_TEXT])
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
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert response.status_code == 200, response.text
                assert response.json()["answer"]
        finally:
            application.dependency_overrides.clear()

        handle = getattr(application.state, "tracing_handle", None)
        assert handle is not None
        assert handle.enabled is False
        assert tracing.active_handle() is None
        assert memory.get_finished_spans() == ()
