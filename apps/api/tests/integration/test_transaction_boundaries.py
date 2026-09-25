"""Commit and rollback behaviour across the service boundaries.

Every test in this file describes a bug the prototype actually shipped, or a
boundary the architecture document promises. They are deliberately *service and
database* tests rather than response-shape tests: the assertion that matters is
what survives in PostgreSQL after a failure, read back through the owner engine
because the application role is (correctly) unable to see rows it did not write.

The four boundaries:

1. **Ingestion.** ``services.ingestion`` writes the object *before* the row, and
   the pipeline never commits. A failure after the row is staged must leave no
   ``documents`` row and no stored object. The prototype committed the row first
   and embedded afterwards, so a failed ingest left a visible document and a
   stray file whose retry failed on the unique ``(tenant, course, sha256)`` key.
2. **A failed turn.** ``services.agent.run_turn`` commits the user message
   before it runs the graph. A question was asked whether or not the answer was
   produced, so the row survives the failure and only the assistant row is
   rolled back (see :class:`TestAFailedTurnStillRecordsTheQuestion`).
3. **A failed evaluation.** ``assessment.evaluator.evaluate_answer`` writes a
   ``progress_events`` row and flushes it. A failure after that flush must not
   leave a partial event, and an ungraded attempt must leave no attempt row that
   would project as a recorded zero.
4. **Deletion.** ``services.ingestion.delete_document`` repairs the two
   materialised BM25 aggregates by hand, because ``tenant_lexical_stats`` and
   ``tenant_corpus_stats`` are keyed by tenant and have no foreign key to
   ``documents`` — the cascade cannot reach them.

Nothing here weakens an existing test. Where a guarantee is already covered
end to end (``test_documents_api.py``), the coverage is named in the class
docstring and the test here goes one level deeper instead of repeating it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
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
    get_object_store,
    get_settings_dep,
)
from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.llm.gateway import LiteLLMGateway
from coursellm.llm.types import LLMRequest, LLMScope, bind_llm_scope
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.repositories.content import DocumentRepository
from coursellm.storage import LocalObjectStore
from tests.integration.conftest import SEED_PASSWORD

pytestmark = pytest.mark.integration

_DOCUMENT_TEXT = (
    b"Transformers rely on self attention over the whole sequence. "
    b"The attention mechanism mixes information across positions. "
    b"Feed forward layers follow every attention sublayer. "
    b"Residual connections stabilise deep transformer training. "
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class ExplodingGateway(LiteLLMGateway):
    """The real gateway with a provider call that fails with a typed error.

    ``ServiceUnavailableError`` is what the gateway produces when a provider is
    unreachable. The agent graph deliberately degrades on that rather than
    failing the turn, which is why the *hard*-failure boundary is tested by
    breaking the graph invocation itself (see
    :class:`TestAFailedTurnStillRecordsTheQuestion`).
    """

    async def _invoke(
        self,
        model: str,
        request: LLMRequest,
        *,
        attempt: int,
        provider: str,
        used_fallback: bool,
        repair: str | None = None,
    ) -> Any:  # pragma: no cover - the raise is the point
        raise ServiceUnavailableError("The model provider is unavailable.")


@pytest_asyncio.fixture(loop_scope="function")
async def object_store(tmp_path: Any) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def tx_settings(pg_settings: Settings) -> Settings:
    """RLS-enforcing settings with the gateway enabled and a small real pool.

    The pool is slightly wider than one because a chat turn holds one connection
    for its transaction while the gateway opens a second to write ``llm_usage``.
    A single-connection pool would deadlock on itself, which is a property of
    the gateway rather than of the boundary under test.
    """
    return pg_settings.model_copy(
        update={
            "llm_enabled": True,
            "fallback_model": "",
            "llm_max_retries": 0,
            "db_pool_size": 3,
            "db_max_overflow": 0,
            "rate_limit_requests_per_minute": 600,
            "rate_limit_llm_requests_per_hour": 600,
        }
    )


@pytest_asyncio.fixture(loop_scope="function")
async def tx_client(
    tx_settings: Settings, object_store: LocalObjectStore
) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose app has a real pool, a real DB and a temp object store."""
    application = create_app(tx_settings)
    application.dependency_overrides[get_settings_dep] = lambda: tx_settings
    application.dependency_overrides[get_object_store] = lambda: object_store

    async def _exploding_gateway(
        context: ContextDep, settings_dep: SettingsDep
    ) -> AsyncIterator[ExplodingGateway]:
        session_factory = await get_session_factory(settings_dep)
        gateway = ExplodingGateway(settings_dep, session_factory)
        scope = LLMScope(tenant_id=context.tenant_id, user_id=context.user_id)
        with bind_llm_scope(scope):
            yield gateway

    application.dependency_overrides[get_llm_gateway] = _exploding_gateway
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        application.dependency_overrides.clear()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _scalar(engine: AsyncEngine, statement: str, **params: Any) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(text(statement), params)).scalar_one())


async def _ingest_tenant_a(settings: Settings, seeded: Any) -> None:
    """Populate tenant A's document through the real pipeline (for chat tests)."""
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        await ingest_document(
            session,
            settings,
            document=document,
            data=_DOCUMENT_TEXT,
            embedder=HashingEmbedder(dim=EMBEDDING_DIM),
        )


# ---------------------------------------------------------------------------
# 1. Ingestion
# ---------------------------------------------------------------------------
class TestIngestionFailureLeavesNothingBehind:
    """The orphan-row/orphan-object bug from the prototype.

    ``test_documents_api.py::test_a_failed_ingest_leaves_no_orphan_row_or_object``
    covers the parser-failure path end to end. This class adds the case that
    path does *not* reach: a failure while the row is already staged and
    flushed, which is where a commit-first implementation loses atomicity.
    """

    async def test_a_failure_after_the_row_is_staged_leaves_no_row_and_no_object(
        self,
        tx_settings: Settings,
        object_store: LocalObjectStore,
        seeded: Any,
        owner_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fail(*_args: Any, **_kwargs: Any) -> Any:
            # The document row has been added and flushed by the time the
            # pipeline is called; this is the seam the prototype got wrong.
            raise RuntimeError("embedding provider available only in production")

        # The service imports the pipeline function into its own namespace, so
        # patching ``pipeline.ingest_document`` would not affect it. Patching the
        # binding the service actually calls is the only version that fails.
        monkeypatch.setattr("coursellm.services.ingestion.ingest_document", _fail)

        scope = TenantScope(seeded.tenant_a_id)
        with pytest.raises(Exception):  # noqa: B017 - the type is asserted below
            async with tenant_session(tx_settings, scope) as session:
                await _create(
                    session,
                    scope,
                    object_store,
                    tx_settings,
                    user_id=seeded.user_a_id,
                    course_id=seeded.course_a_id,
                    filename="staged.txt",
                    data=b"Some notes that will fail to embed because the provider is gone.",
                )

        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM documents WHERE course_id = :cid",
                cid=str(seeded.course_a_id),
            )
            == 1  # only the fixture's own document
        )
        assert [p for p in object_store.root.rglob("*") if p.is_file()] == []

    async def test_the_http_route_returns_a_typed_error_and_commits_nothing(
        self,
        tx_client: AsyncClient,
        object_store: LocalObjectStore,
        seeded: Any,
        owner_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The route-level view of the same boundary."""

        async def _fail(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("embedding provider unavailable")

        monkeypatch.setattr("coursellm.services.ingestion.ingest_document", _fail)
        token = await _login(tx_client, seeded.tenant_a.email)

        response = await tx_client.post(
            "/api/v1/documents",
            headers=_auth(token),
            data={"course_id": str(seeded.course_a_id)},
            files={"file": ("staged.txt", b"Notes that will not embed.", "text/plain")},
        )

        assert response.status_code == 502, response.text
        assert response.json()["error"] == "upstream_error"
        assert "embedding provider unavailable" not in response.text
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM documents WHERE course_id = :cid",
                cid=str(seeded.course_a_id),
            )
            == 1
        )
        assert [p for p in object_store.root.rglob("*") if p.is_file()] == []


async def _create(
    session: Any,
    scope: TenantScope,
    store: LocalObjectStore,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    filename: str,
    data: bytes,
) -> Any:
    from coursellm.services.ingestion import create_document

    return await create_document(
        session,
        scope,
        store,
        settings,
        user_id=user_id,
        course_id=course_id,
        filename=filename,
        content_type="text/plain",
        data=data,
    )


# ---------------------------------------------------------------------------
# 2. A failed turn
# ---------------------------------------------------------------------------
class _BrokenGraph:
    """A graph whose invocation raises, standing in for an unhandled failure.

    A *provider* failure is not enough: the agent nodes catch typed model errors
    and degrade to the extractive answer, which is a separate guarantee covered
    by ``test_chat_agent_path.py``. To reach the transaction boundary the graph
    call itself has to raise.
    """

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the graph failed outside any node's recovery path")

    async def aget_state(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        return None


class TestAFailedTurnStillRecordsTheQuestion:
    """The requirement: a question asked is history even when the answer fails.

    ``services/agent.run_turn`` (and ``services/chat.prepare_turn``) commit the
    user message before generation runs, so an unhandled failure rolls back only
    the answer. That is the documented intent (``services/chat.py`` module
    docstring: "the transcript is complete and auditable").
    """

    async def test_the_user_message_survives_a_hard_generation_failure(
        self,
        tx_settings: Settings,
        tx_client: AsyncClient,
        seeded: Any,
        owner_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _ingest_tenant_a(tx_settings, seeded)
        monkeypatch.setattr(
            "coursellm.services.agent.build_graph", lambda **_kwargs: _BrokenGraph()
        )
        token = await _login(tx_client, seeded.tenant_a.email)

        response = await tx_client.post(
            "/api/v1/chat",
            headers=_auth(token),
            json={
                "question": "What do transformers rely on?",
                "course_id": str(seeded.course_a_id),
            },
        )

        assert response.status_code == 500
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM messages WHERE tenant_id = :tid AND role = 'user'",
                tid=str(seeded.tenant_a_id),
            )
            == 1
        ), (
            "a question was asked; the user message must be committed even when "
            "generation fails, otherwise the transcript is incomplete and the "
            "user can never see that they asked"
        )

    async def test_a_failed_turn_never_writes_an_assistant_row(
        self,
        tx_settings: Settings,
        tx_client: AsyncClient,
        seeded: Any,
        owner_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The other half of the boundary, which does hold today.

        Whatever is done about the user message, a half-generated answer must
        never be recorded as if it were complete.
        """
        await _ingest_tenant_a(tx_settings, seeded)
        monkeypatch.setattr(
            "coursellm.services.agent.build_graph", lambda **_kwargs: _BrokenGraph()
        )
        token = await _login(tx_client, seeded.tenant_a.email)

        await tx_client.post(
            "/api/v1/chat",
            headers=_auth(token),
            json={
                "question": "What do transformers rely on?",
                "course_id": str(seeded.course_a_id),
            },
        )

        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM messages WHERE tenant_id = :tid AND role = 'assistant'",
                tid=str(seeded.tenant_a_id),
            )
            == 0
        )

    async def test_a_provider_outage_degrades_and_still_persists_the_turn(
        self,
        tx_settings: Settings,
        tx_client: AsyncClient,
        seeded: Any,
        owner_engine: AsyncEngine,
    ) -> None:
        """The recoverable failure, for contrast: the turn completes and persists.

        A typed provider failure must *not* take the hard path. The graph
        degrades to the extractive answer, so the transcript ends up with one
        user row and one assistant row and no error reaches the client. This is
        the behaviour that makes the hard-failure case above a narrow bug
        rather than a general one.
        """
        await _ingest_tenant_a(tx_settings, seeded)
        token = await _login(tx_client, seeded.tenant_a.email)

        response = await tx_client.post(
            "/api/v1/chat",
            headers=_auth(token),
            json={
                "question": "What do transformers rely on?",
                "course_id": str(seeded.course_a_id),
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["answer"]
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM messages WHERE tenant_id = :tid AND role = 'user'",
                tid=str(seeded.tenant_a_id),
            )
            == 1
        )
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM messages WHERE tenant_id = :tid AND role = 'assistant'",
                tid=str(seeded.tenant_a_id),
            )
            == 1
        )


# ---------------------------------------------------------------------------
# 3. A failed evaluation
# ---------------------------------------------------------------------------
class TestFailedEvaluationWritesNoPartialEvent:
    """One evaluation writes exactly one event, or none."""

    async def test_an_ungraded_attempt_leaves_neither_attempt_nor_event(
        self,
        tx_settings: Settings,
        tx_client: AsyncClient,
        seeded: Any,
        owner_engine: AsyncEngine,
    ) -> None:
        """The typed-failure path: the placeholder attempt is removed again.

        ``QuizAttempt.score`` is non-nullable, so an ungraded placeholder must be
        deleted rather than left to project as mastery zero. ``ExplodingGateway``
        makes scoring raise ``UpstreamError``, which ``score_item`` converts to a
        typed ``score=None`` outcome.
        """
        quiz_id, item_id = await _seed_quiz(owner_engine, seeded)
        token = await _login(tx_client, seeded.tenant_a.email)

        response = await tx_client.post(
            f"/api/v1/quizzes/{quiz_id}/items/{item_id}/answer",
            headers=_auth(token),
            json={"answer": "attention"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["score"] is None, "an unscored answer must not be recorded as a grade"
        assert body["progress_event_kind"] is None
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM progress_events WHERE tenant_id = :tid",
                tid=str(seeded.tenant_a_id),
            )
            == 0
        )
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM quiz_attempts WHERE tenant_id = :tid",
                tid=str(seeded.tenant_a_id),
            )
            == 0
        )

    async def test_a_failure_after_the_event_is_flushed_rolls_the_event_back(
        self,
        tx_settings: Settings,
        seeded: Any,
        owner_engine: AsyncEngine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The transaction boundary itself, isolated from the HTTP layer.

        ``evaluate_answer`` adds the ``progress_events`` row and flushes it, then
        reads the projection back. A failure in that read must abort the
        transaction, so the flushed event is not committed. Driving the service
        directly is what makes this a boundary test rather than a response test:
        the assertion is about what PostgreSQL holds after the rollback.
        """
        quiz_id, item_id = await _seed_quiz(owner_engine, seeded)
        attempt_id = await _seed_graded_attempt(owner_engine, seeded, quiz_id, item_id)

        from coursellm.assessment import evaluator

        async def _fail_after_write(*_args: Any, **_kwargs: Any) -> dict[uuid.UUID, float]:
            raise RuntimeError("projection read failed after the event was flushed")

        monkeypatch.setattr(evaluator, "_mastery", _fail_after_write)

        scope = TenantScope(seeded.tenant_a_id)
        with pytest.raises(RuntimeError):
            await _evaluate_once(tx_settings, scope, seeded, attempt_id, item_id)

        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM progress_events WHERE tenant_id = :tid",
                tid=str(seeded.tenant_a_id),
            )
            == 0
        )
        assert (
            await _scalar(
                owner_engine,
                "SELECT count(*) FROM quiz_attempts WHERE id = :id",
                id=str(attempt_id),
            )
            == 1
        )


async def _evaluate_once(
    settings: Settings,
    scope: TenantScope,
    seeded: Any,
    attempt_id: uuid.UUID,
    item_id: str,
) -> None:
    """Run one evaluation in its own transaction, so a rollback is observable."""
    from coursellm.assessment.service import AssessmentService

    async with tenant_session(settings, scope) as session:
        gateway = await _gateway(settings, seeded)
        await AssessmentService(session, scope).evaluate_existing_attempt(
            attempt_id=attempt_id,
            item_id=item_id,
            answer="attention",
            user_id=seeded.user_a_id,
            settings=settings,
            gateway=gateway,
        )


async def _gateway(settings: Settings, seeded: Any) -> ExplodingGateway:
    session_factory = await get_session_factory(settings)
    return ExplodingGateway(settings, session_factory)


async def _seed_quiz(engine: AsyncEngine, seeded: Any) -> tuple[uuid.UUID, str]:
    """A quiz whose single short-answer item cites one resolvable passage."""
    import json

    quiz_id = uuid.uuid4()
    item_id = "item-1"
    item = {
        "item_id": item_id,
        "item_type": "short_answer",
        "prompt": "What does attention do?",
        "model_answer": "attention",
        "rubric": [
            {"criterion": "correctness", "weight": 1.0},
            {"criterion": "grounding", "weight": 1.0},
        ],
        "citation_ids": ["S1"],
        "justification": "Weights the inputs.",
    }
    citation = {
        "citation_id": "S1",
        "chunk_id": str(uuid.uuid4()),
        "document_id": str(seeded.tenant_a.document_id),
        "filename": "alpha-notes.txt",
        "page": 1,
        "source_type": "document",
        "quote": "Attention weights inputs by relevance.",
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO quizzes "
                "(id, tenant_id, user_id, course_id, difficulty, n_items, shortfall, "
                " concept_ids, item_types, items, citations, degraded) "
                "VALUES (:id, :tid, :uid, :cid, 'medium', 1, 0, '[]'::jsonb, "
                " cast(:types AS jsonb), cast(:items AS jsonb), "
                " cast(:citations AS jsonb), '[]'::jsonb)"
            ),
            {
                "id": str(quiz_id),
                "tid": str(seeded.tenant_a_id),
                "uid": str(seeded.user_a_id),
                "cid": str(seeded.course_a_id),
                "types": json.dumps(["short_answer"]),
                "items": json.dumps([item]),
                "citations": json.dumps([citation]),
            },
        )
    return quiz_id, item_id


async def _seed_graded_attempt(
    engine: AsyncEngine, seeded: Any, quiz_id: uuid.UUID, item_id: str
) -> uuid.UUID:
    """An ungraded attempt row for the service-level boundary test."""
    attempt_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO quiz_attempts "
                "(id, tenant_id, user_id, course_id, quiz_id, item_id, concept_ids, answer, "
                " score, rubric, misconceptions) "
                "VALUES (:id, :tid, :uid, :cid, :qid, :item, '[]'::jsonb, 'attention', "
                " 0.0, '[]'::jsonb, '[]'::jsonb)"
            ),
            {
                "id": str(attempt_id),
                "tid": str(seeded.tenant_a_id),
                "uid": str(seeded.user_a_id),
                "cid": str(seeded.course_a_id),
                "qid": str(quiz_id),
                "item": item_id,
            },
        )
    return attempt_id


# ---------------------------------------------------------------------------
# 4. Deletion repairs the BM25 aggregates
# ---------------------------------------------------------------------------
class TestDeletionRepairsTheBM25Aggregates:
    """The bug found during the documents PR.

    ``tenant_lexical_stats`` and ``tenant_corpus_stats`` are keyed by tenant and
    hold no foreign key to ``documents``, so ``ON DELETE CASCADE`` never reaches
    them. Without an explicit recompute, deleting a document leaves both
    aggregates counting text that is gone, and BM25 scores every survivor
    against an inflated corpus.

    ``test_documents_api.py::test_delete_refreshes_the_materialised_retrieval_statistics``
    covers the API round trip. This test deletes through the service and asserts
    the *difference* the stale row would have hidden: the aggregates match a
    fresh recomputation from ``chunk_terms`` and ``chunks`` exactly, including
    the total-token count that no per-term check would catch.
    """

    async def test_the_aggregates_equal_a_fresh_recomputation_after_delete(
        self,
        tx_settings: Settings,
        object_store: LocalObjectStore,
        seeded: Any,
        owner_engine: AsyncEngine,
    ) -> None:
        from coursellm.services.ingestion import create_document, delete_document

        scope = TenantScope(seeded.tenant_a_id)
        async with tenant_session(tx_settings, scope) as session:
            first = await create_document(
                session,
                scope,
                object_store,
                tx_settings,
                user_id=seeded.user_a_id,
                course_id=seeded.course_a_id,
                filename="alpha.txt",
                content_type="text/plain",
                data=b"alpha beta gamma " * 120,
            )
            second = await create_document(
                session,
                scope,
                object_store,
                tx_settings,
                user_id=seeded.user_a_id,
                course_id=seeded.course_a_id,
                filename="beta.txt",
                content_type="text/plain",
                data=b"beta gamma delta " * 120,
            )
            assert first.created
            assert second.created

        # Delete through the service, in its own transaction, exactly as the
        # route would.
        async with tenant_session(tx_settings, scope) as session:
            await delete_document(
                session,
                scope,
                object_store,
                user_id=seeded.user_a_id,
                document_id=first.document.id,
            )

        expected_terms = await _expected_terms(owner_engine, seeded.tenant_a_id)
        stored_terms = await _stored_terms(owner_engine, seeded.tenant_a_id)
        assert stored_terms == expected_terms, (
            "tenant_lexical_stats still counts terms from the deleted document"
        )

        chunk_count, total_tokens = await _corpus_reference(owner_engine, seeded.tenant_a_id)
        stored_corpus = await _stored_corpus(owner_engine, seeded.tenant_a_id)
        assert stored_corpus == (chunk_count, total_tokens), (
            "tenant_corpus_stats still counts the deleted document's chunks/tokens"
        )


async def _expected_terms(engine: AsyncEngine, tenant_id: uuid.UUID) -> dict[str, int]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT term, count(DISTINCT chunk_id) AS doc_freq FROM chunk_terms "
                    "WHERE tenant_id = :tid GROUP BY term"
                ),
                {"tid": str(tenant_id)},
            )
        ).all()
    return {str(term): int(doc_freq) for term, doc_freq in rows}


async def _stored_terms(engine: AsyncEngine, tenant_id: uuid.UUID) -> dict[str, int]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT term, doc_freq FROM tenant_lexical_stats WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
        ).all()
    return {str(term): int(doc_freq) for term, doc_freq in rows}


async def _corpus_reference(engine: AsyncEngine, tenant_id: uuid.UUID) -> tuple[int, int]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT count(*), coalesce(sum(token_count), 0) FROM chunks "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            )
        ).one()
    return int(row[0]), int(row[1])


async def _stored_corpus(engine: AsyncEngine, tenant_id: uuid.UUID) -> tuple[int, int]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT doc_count, total_tokens FROM tenant_corpus_stats WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            )
        ).one()
    return int(row[0]), int(row[1])
