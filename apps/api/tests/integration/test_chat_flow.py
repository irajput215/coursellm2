"""The full direct RAG chat flow against real PostgreSQL, with a scripted gateway.

The agent graph is the default engine for ``/chat``; this module is specifically
about the direct path, so every request names ``"engine": "rag"`` explicitly.
``test_chat_agent_path.py`` covers the agent engine and the equivalence of the
two engines' citations.

Nothing here mocks ``litellm``. :class:`ScriptedGateway` subclasses the real
:class:`~coursellm.llm.gateway.LiteLLMGateway` and overrides only the provider
call, so the retry, validation and **usage persistence** paths under test are the
production ones. That is what makes the ``llm_usage`` assertion meaningful.

Documents are seeded with PR 4's ingestion pipeline so the BM25 statistics and
embeddings are populated exactly as they are in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.repositories.content import DocumentRepository

pytestmark = pytest.mark.integration

# Matches ``SEED_PASSWORD`` in the integration conftest.
SEED_PASSWORD = "seed-password-123"
DOCUMENT_FILENAME = "alpha-notes.txt"
QUESTION = "What do transformers rely on?"
DOCUMENT_TEXT = (
    b"Transformers rely on self attention over the whole sequence. "
    b"The attention mechanism mixes information across positions. "
    b"Feed forward layers follow every attention sublayer. "
    b"Residual connections stabilise deep transformer training. "
    b"Positional encodings tell the model about token order. "
)


async def _no_sleep(_seconds: float) -> None:
    return None


class ScriptedGateway(LiteLLMGateway):
    """The real gateway with the provider call replaced by a script.

    Accounting, routing and response normalisation are inherited unchanged, so a
    successful answer writes exactly one ``llm_usage`` row through the ambient
    tenant scope.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        *,
        texts: list[str],
    ) -> None:
        super().__init__(settings, session_factory, sleep=_no_sleep)
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
        return _Completion(
            text=self._next_text(),
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
            latency_ms=1.0,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        texts, self._texts = self._texts, []
        for chunk in texts:
            for line in chunk.splitlines(keepends=True):
                yield line

    def _next_text(self) -> str:
        if not self._texts:
            raise AssertionError(
                "The scripted gateway received more calls than it was given texts."
            )
        return self._texts.pop(0)


@dataclass
class Script:
    """Mutable per-test script handed to the scripted gateway."""

    texts: list[str] = field(default_factory=list)


@pytest.fixture
def script() -> Script:
    return Script()


@pytest.fixture
def chat_settings(pg_settings: Settings) -> Settings:
    """RLS-enforcing settings with the gateway enabled and a real pool.

    The pool is widened because the request holds one connection while the
    gateway opens a second to persist usage; with ``db_pool_size=1`` that second
    checkout would wait forever.
    """
    return pg_settings.model_copy(
        update={
            "llm_enabled": True,
            "fallback_model": "",
            "llm_max_retries": 0,
            "db_pool_size": 5,
            "db_max_overflow": 0,
        }
    )


@pytest_asyncio.fixture(loop_scope="function")
async def chat(chat_settings: Settings, script: Script) -> AsyncIterator[AsyncClient]:
    application = create_app(chat_settings)
    application.dependency_overrides[get_settings_dep] = lambda: chat_settings

    async def _gateway_override(
        context: ContextDep, settings_dep: SettingsDep
    ) -> AsyncIterator[ScriptedGateway]:
        session_factory = await get_session_factory(settings_dep)
        gateway = ScriptedGateway(settings_dep, session_factory, texts=script.texts)
        scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
        with bind_llm_scope(scope):
            yield gateway

    application.dependency_overrides[get_llm_gateway] = _gateway_override
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        application.dependency_overrides.clear()


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ingest_tenant_a(settings: Settings, seeded: Any) -> None:
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


async def _usage_rows(owner_engine: AsyncEngine, tenant_id: Any) -> int:
    async with owner_engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM llm_usage WHERE tenant_id = :tenant_id"),
                    {"tenant_id": str(tenant_id)},
                )
            ).scalar_one()
        )


async def test_in_corpus_question_returns_verified_citations_and_persists_the_turn(
    chat_settings: Settings,
    seeded: Any,
    owner_engine: AsyncEngine,
    chat: AsyncClient,
    script: Script,
) -> None:
    await _ingest_tenant_a(chat_settings, seeded)
    script.texts = ["Transformers rely on self attention over the whole sequence. [S1]"]
    token = await _login(chat, seeded.tenant_a.email)

    response = await chat.post(
        "/api/v1/chat",
        json={"question": QUESTION, "course_id": str(seeded.tenant_a.course_id), "engine": "rag"},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grounded"] is True
    assert body["degraded"] == []
    assert body["usage"]["total_tokens"] == 15
    assert body["citations"], body
    citation = body["citations"][0]
    assert citation["citation_id"] == "S1"
    assert citation["document_id"] == str(seeded.tenant_a.document_id)
    assert citation["filename"] == DOCUMENT_FILENAME
    assert citation["source_type"] == "other"

    detail = await chat.get(
        f"/api/v1/chat/conversations/{body['conversation_id']}", headers=_auth(token)
    )
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == QUESTION
    assert messages[1]["citations"][0]["document_id"] == str(seeded.tenant_a.document_id)
    assert messages[1]["grounded"] is True

    assert await _usage_rows(owner_engine, seeded.tenant_a_id) == 1


async def test_out_of_corpus_question_refuses_without_calling_the_model(
    chat_settings: Settings,
    seeded: Any,
    owner_engine: AsyncEngine,
    chat: AsyncClient,
    script: Script,
) -> None:
    # No ingestion: the course has a document row but no chunks, so retrieval
    # returns nothing and the refusal must be composed without a model call.
    script.texts = ["this text must never be requested"]
    token = await _login(chat, seeded.tenant_a.email)

    response = await chat.post(
        "/api/v1/chat",
        json={
            "question": "Explain the role of quantum chromodynamics in photosynthesis.",
            "course_id": str(seeded.tenant_a.course_id),
            "engine": "rag",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["grounded"] is False
    assert body["citations"] == []
    assert "no_evidence" in body["degraded"]
    assert "don't have enough information" in body["answer"]
    assert await _usage_rows(owner_engine, seeded.tenant_a_id) == 0


async def test_conversations_are_not_visible_to_another_tenant(
    chat_settings: Settings,
    seeded: Any,
    chat: AsyncClient,
    script: Script,
) -> None:
    script.texts = ["unused"]
    token_a = await _login(chat, seeded.tenant_a.email)
    created = await chat.post(
        "/api/v1/chat",
        json={
            "question": "Hello there",
            "course_id": str(seeded.tenant_a.course_id),
            "engine": "rag",
        },
        headers=_auth(token_a),
    )
    assert created.status_code == 200, created.text
    conversation_id = created.json()["conversation_id"]

    token_b = await _login(chat, seeded.tenant_b.email)
    stolen = await chat.get(f"/api/v1/chat/conversations/{conversation_id}", headers=_auth(token_b))
    assert stolen.status_code == 404

    listing = await chat.get("/api/v1/chat/conversations", headers=_auth(token_b))
    assert listing.status_code == 200
    assert listing.json() == []

    own = await chat.get("/api/v1/chat/conversations", headers=_auth(token_a))
    assert own.status_code == 200
    assert len(own.json()) == 1


async def test_over_long_question_is_rejected_before_retrieval(
    chat_settings: Settings,
    seeded: Any,
    chat: AsyncClient,
    script: Script,
) -> None:
    script.texts = []
    token = await _login(chat, seeded.tenant_a.email)

    response = await chat.post(
        "/api/v1/chat",
        json={"question": "x" * (chat_settings.max_query_chars + 1)},
        headers=_auth(token),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


async def test_streaming_endpoint_emits_tokens_citations_and_done(
    chat_settings: Settings,
    seeded: Any,
    chat: AsyncClient,
    script: Script,
) -> None:
    await _ingest_tenant_a(chat_settings, seeded)
    script.texts = ["Self attention mixes information ", "across positions [S1]."]
    token = await _login(chat, seeded.tenant_a.email)

    response = await chat.post(
        "/api/v1/chat/stream",
        json={"question": QUESTION, "course_id": str(seeded.tenant_a.course_id), "engine": "rag"},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.text
    assert "event: token" in body
    assert "event: citations" in body
    assert "event: done" in body
    assert "across positions" in body
    assert str(seeded.tenant_a.document_id) in body
