"""End-to-end assessment flow against real PostgreSQL with the RLS-enforcing role.

Corpus, concepts and edges are seeded through the owner engine (a fixture must
bypass Row-Level Security); every request runs as ``coursellm_app``, so a missing
policy or a missing tenant filter fails the test rather than passing for the
wrong reason. The model is a scripted gateway injected over the request
dependency, so generation and grading are deterministic without a provider.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.assessment.schemas import (
    GeneratedCriterion,
    GeneratedMisconception,
    GeneratedQuiz,
    GeneratedQuizItem,
    GeneratedScore,
)
from coursellm.assessment.service import AssessmentService
from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.llm import LLMRequest, LLMResponse
from coursellm.security.passwords import hash_password
from tests.integration.conftest import SEED_PASSWORD, Seeded

pytestmark = pytest.mark.integration

API = "/api/v1"
CHUNK_CONTENT = (
    "Attention is a mechanism that weights inputs by relevance, so the model "
    "can focus on the parts of the sequence that matter most."
)
CHUNK_TERMS = {"attention": 2, "mechanism": 1, "weights": 1, "inputs": 1, "relevance": 1}


# ---------------------------------------------------------------------------
# The scripted model
# ---------------------------------------------------------------------------
def _llm_response(parsed: Any) -> LLMResponse:
    return LLMResponse(
        text=parsed.model_dump_json(),
        parsed=parsed,
        model="scripted",
        provider="test",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost_usd=0.0,
        latency_ms=0.0,
        cached=False,
        fallback_used=False,
        finish_reason="stop",
    )


class ScriptedAssessmentGateway:
    """Deterministic quiz generation and rubric scoring for the integration test.

    Generation cites ``S1`` and copies the first candidate concept id out of the
    request, which is the only way a scripted double can exercise the real
    grounding and concept-linkage paths. Scoring returns a deliberately wrong
    ``total`` so the assertion that the recorded score is computed in Python is
    meaningful at the API boundary too.
    """

    def __init__(self) -> None:
        self.quiz_calls = 0
        self.score_calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.response_model is GeneratedQuiz:
            self.quiz_calls += 1
            return _llm_response(GeneratedQuiz(items=[self._quiz_item(request)]))
        if request.response_model is GeneratedScore:
            self.score_calls += 1
            return _llm_response(self._score(request))
        raise AssertionError(f"unexpected response model {request.response_model!r}")

    async def stream(self, request: LLMRequest):  # pragma: no cover - not used
        yield ""

    def _quiz_item(self, request: LLMRequest) -> GeneratedQuizItem:
        text_body = "\n".join(message.content for message in request.messages)
        match = re.search(r'"concept_id":\s*"([0-9a-fA-F-]{36})"', text_body)
        concept_id = match.group(1) if match else None
        return GeneratedQuizItem(
            item_type="short_answer",
            prompt="What does attention do?",
            model_answer="attention",
            citation_ids=["S1"],
            concept_id=concept_id,
            justification="It weights the inputs by relevance.",
        )

    def _score(self, request: LLMRequest) -> GeneratedScore:
        user_text = request.messages[-1].content if request.messages else ""
        match = re.search(r'Student answer: "(.*)"', user_text)
        student = match.group(1) if match else ""
        correct = "attention" in student
        criteria = [
            GeneratedCriterion(
                criterion=criterion,
                score=1.0 if correct else 0.0,
                justification="Correct." if correct else "Missed the mechanism.",
            )
            for criterion in ("correctness", "reasoning", "grounding")
        ]
        misconceptions = (
            []
            if correct
            else [
                GeneratedMisconception(
                    misconception_type="attention_is_lookup",
                    description="The answer treats attention as a lookup table.",
                    corrected_statement="Attention computes weighted combinations.",
                    severity="high",
                    citation_ids=["S1"],
                )
            ]
        )
        # Deliberately wrong arithmetic: the evaluator must ignore it.
        return GeneratedScore(
            criteria=criteria,
            misconceptions=misconceptions,
            total=0.25 if correct else 0.75,
        )


def _patch_gateway(monkeypatch: pytest.MonkeyPatch, gateway: ScriptedAssessmentGateway) -> None:
    monkeypatch.setattr("coursellm.api.deps.get_gateway", lambda *args, **kwargs: gateway)


# ---------------------------------------------------------------------------
# Seeding helpers (owner engine: a fixture must bypass RLS)
# ---------------------------------------------------------------------------
async def _insert_chunk(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    content: str,
    terms: Mapping[str, int],
    token_count: int,
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO chunks (id, tenant_id, document_id, course_id, content, page, "
                "chunk_index, token_count, topic, starts_mid_sentence) "
                "VALUES (:id, :tenant_id, :document_id, :course_id, :content, 1, 0, "
                ":token_count, 'attention', false)"
            ),
            {
                "id": str(chunk_id),
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "course_id": str(course_id),
                "content": content,
                "token_count": token_count,
            },
        )
        for term, tf in terms.items():
            await connection.execute(
                text(
                    "INSERT INTO chunk_terms (chunk_id, tenant_id, term, tf) "
                    "VALUES (:chunk_id, :tenant_id, :term, :tf)"
                ),
                {
                    "chunk_id": str(chunk_id),
                    "tenant_id": str(tenant_id),
                    "term": term,
                    "tf": tf,
                },
            )
    return chunk_id


async def _rebuild_stats(engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM tenant_lexical_stats WHERE tenant_id = CAST(:t AS uuid)"),
            {"t": str(tenant_id)},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_lexical_stats (tenant_id, term, doc_freq) "
                "SELECT tenant_id, term, count(DISTINCT chunk_id) FROM chunk_terms "
                "WHERE tenant_id = CAST(:t AS uuid) GROUP BY tenant_id, term"
            ),
            {"t": str(tenant_id)},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_corpus_stats "
                "(tenant_id, doc_count, total_tokens, avg_doc_len) "
                "SELECT CAST(:t AS uuid), count(*), coalesce(sum(token_count), 0), "
                "coalesce(avg(token_count), 0)::float FROM chunks "
                "WHERE tenant_id = CAST(:t AS uuid) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "doc_count = EXCLUDED.doc_count, "
                "total_tokens = EXCLUDED.total_tokens, "
                "avg_doc_len = EXCLUDED.avg_doc_len"
            ),
            {"t": str(tenant_id)},
        )


async def _insert_concept(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    name: str,
    slug: str,
    difficulty: int,
) -> uuid.UUID:
    concept_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO concepts "
                "(id, tenant_id, course_id, name, slug, difficulty, confidence, verified) "
                "VALUES (:id, :tenant_id, :course_id, :name, :slug, :difficulty, 0.9, false)"
            ),
            {
                "id": str(concept_id),
                "tenant_id": str(tenant_id),
                "course_id": str(course_id),
                "name": name,
                "slug": slug,
                "difficulty": difficulty,
            },
        )
    return concept_id


async def _insert_edge(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    provenance_chunk_id: uuid.UUID,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO concept_edges "
                "(id, tenant_id, source_concept_id, target_concept_id, relation, weight, "
                " confidence, corroboration_count, cue, verified, provenance_chunk_id) "
                "VALUES (:id, :tenant_id, :source, :target, 'requires', 1.0, 0.9, 2, "
                " 'explicit', false, :chunk)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "source": str(source_id),
                "target": str(target_id),
                "chunk": str(provenance_chunk_id),
            },
        )


async def _insert_user(
    engine: AsyncEngine, *, tenant_id: uuid.UUID, email: str, label: str
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, email, hashed_password, full_name, role, is_active) "
                "VALUES (:id, :tenant_id, :email, :hashed, :full_name, 'member', true)"
            ),
            {
                "id": str(user_id),
                "tenant_id": str(tenant_id),
                "email": email,
                "hashed": hash_password(SEED_PASSWORD),
                "full_name": label,
            },
        )
    return user_id


async def _seed_corpus(
    engine: AsyncEngine, seeded: Seeded
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One chunk linked to a concept, plus a second concept to form an edge."""
    chunk_id = await _insert_chunk(
        engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        document_id=seeded.tenant_a.document_id,
        content=CHUNK_CONTENT,
        terms=CHUNK_TERMS,
        token_count=18,
    )
    await _rebuild_stats(engine, seeded.tenant_a_id)
    attention = await _insert_concept(
        engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        name="Attention",
        slug="attention",
        difficulty=1,
    )
    transformers = await _insert_concept(
        engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        name="Transformers",
        slug="transformers",
        difficulty=2,
    )
    await _insert_edge(
        engine,
        tenant_id=seeded.tenant_a_id,
        source_id=attention,
        target_id=transformers,
        provenance_chunk_id=chunk_id,
    )
    return chunk_id, attention, transformers


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _generate(
    client: AsyncClient,
    headers: Mapping[str, str],
    course_id: uuid.UUID,
    *,
    n_items: int = 1,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/quizzes",
        json={
            "course_id": str(course_id),
            "n_items": n_items,
            "item_types": ["short_answer"],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _answer(
    client: AsyncClient,
    headers: Mapping[str, str],
    quiz_id: str,
    item_id: str,
    answer: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/quizzes/{quiz_id}/items/{item_id}/answer",
        json={"answer": answer},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _event_rows(engine: AsyncEngine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT kind, concept_id, mastery FROM progress_events "
                        "WHERE tenant_id = :tenant_id ORDER BY occurred_at"
                    ),
                    {"tenant_id": str(tenant_id)},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def _attempt_rows(engine: AsyncEngine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT id, item_id, answer, score, rubric, misconceptions "
                        "FROM quiz_attempts WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(tenant_id)},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
async def test_generate_quiz_grounds_every_item_in_the_document(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_corpus(owner_engine, seeded)
    gateway = ScriptedAssessmentGateway()
    _patch_gateway(monkeypatch, gateway)
    headers = await _login(api_client, seeded.tenant_a.email)

    body = await _generate(api_client, headers, seeded.course_a_id)

    assert gateway.quiz_calls == 1
    assert body["shortfall"] == 0
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["citation_ids"] == ["S1"]
    assert item["concept_id"] is not None
    assert body["citations"][0]["document_id"] == str(seeded.tenant_a.document_id)

    fetched = await api_client.get(f"{API}/quizzes/{body['quiz_id']}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["items"] == body["items"]


async def test_a_correct_answer_scores_and_writes_one_event(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_corpus(owner_engine, seeded)
    _patch_gateway(monkeypatch, ScriptedAssessmentGateway())
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _generate(api_client, headers, seeded.course_a_id)
    item_id = body["items"][0]["item_id"]

    result = await _answer(api_client, headers, body["quiz_id"], item_id, "attention")

    assert result["score"] == 1.0  # computed, not the gateway's 0.25
    assert result["progress_event_kind"] == "quiz_attempt"
    assert result["mastery_delta"] > 0
    events = await _event_rows(owner_engine, seeded.tenant_a_id)
    assert len(events) == 1
    assert events[0]["kind"] == "quiz_attempt"
    assert float(events[0]["mastery"]) == pytest.approx(1.0)
    attempts = await _attempt_rows(owner_engine, seeded.tenant_a_id)
    assert len(attempts) == 1
    assert attempts[0]["answer"] == "attention"
    assert float(attempts[0]["score"]) == pytest.approx(1.0)
    assert attempts[0]["rubric"]


async def test_a_below_threshold_answer_writes_concept_struggled(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_corpus(owner_engine, seeded)
    _patch_gateway(monkeypatch, ScriptedAssessmentGateway())
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _generate(api_client, headers, seeded.course_a_id)
    item_id = body["items"][0]["item_id"]

    result = await _answer(api_client, headers, body["quiz_id"], item_id, "unrelated")

    assert result["score"] == 0.0
    assert result["progress_event_kind"] == "concept_struggled"
    events = await _event_rows(owner_engine, seeded.tenant_a_id)
    assert len(events) == 1
    assert events[0]["kind"] == "concept_struggled"
    attempts = await _attempt_rows(owner_engine, seeded.tenant_a_id)
    assert len(attempts) == 1
    assert attempts[0]["rubric"]
    assert attempts[0]["misconceptions"]
    payload = json.loads(attempts[0]["misconceptions"][0])
    assert payload["misconception_type"] == "attention_is_lookup"
    assert payload["citation_ids"] == ["S1"]


async def test_progress_summary_reflects_attempts_and_recommends_an_action(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_corpus(owner_engine, seeded)
    _patch_gateway(monkeypatch, ScriptedAssessmentGateway())
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _generate(api_client, headers, seeded.course_a_id)
    await _answer(api_client, headers, body["quiz_id"], body["items"][0]["item_id"], "unrelated")

    summary = await api_client.get(f"{API}/progress/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload["mastery"]
    assert payload["attempt_counts"]
    assert len(payload["recent_attempts"]) == 1
    assert payload["next_action"] is not None
    assert payload["next_action"]["kind"] == "review_concept"

    history = await api_client.get(f"{API}/progress/attempts", headers=headers)
    assert history.status_code == 200, history.text
    page = history.json()
    assert page["total"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["item_id"] == body["items"][0]["item_id"]


async def test_quizzes_and_attempts_are_isolated_by_tenant_and_user(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_corpus(owner_engine, seeded)
    gateway = ScriptedAssessmentGateway()
    _patch_gateway(monkeypatch, gateway)
    headers_a = await _login(api_client, seeded.tenant_a.email)
    headers_b = await _login(api_client, seeded.tenant_b.email)
    created = await _generate(api_client, headers_a, seeded.course_a_id)
    item_id = created["items"][0]["item_id"]
    await _answer(api_client, headers_a, created["quiz_id"], item_id, "attention")

    # Another tenant cannot read or answer A's quiz.
    cross_read = await api_client.get(f"{API}/quizzes/{created['quiz_id']}", headers=headers_b)
    assert cross_read.status_code == 404
    cross_answer = await api_client.post(
        f"{API}/quizzes/{created['quiz_id']}/items/{item_id}/answer",
        json={"answer": "attention"},
        headers=headers_b,
    )
    assert cross_answer.status_code == 404

    # Another user in the same tenant cannot read A's quiz either.
    second_user_id = await _insert_user(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        email="alpha-second@example.com",
        label="Alpha Second",
    )
    headers_second = await _login(api_client, "alpha-second@example.com")
    same_tenant_read = await api_client.get(
        f"{API}/quizzes/{created['quiz_id']}", headers=headers_second
    )
    assert same_tenant_read.status_code == 404

    # Another user's recorded attempt cannot be answered, even though the same
    # tenant can see the row. The service runs on a tenant-scoped session, so the
    # ownership filter is the control under test.
    attempts = await _attempt_rows(owner_engine, seeded.tenant_a_id)
    assert len(attempts) == 1
    attempt_id = uuid.UUID(str(attempts[0]["id"]))
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        service = AssessmentService(session, TenantScope(seeded.tenant_a_id))
        with pytest.raises(NotFoundError):
            await service.evaluate_existing_attempt(
                attempt_id=attempt_id,
                item_id=item_id,
                answer="attention",
                user_id=second_user_id,
                settings=pg_settings,
                gateway=gateway,
            )
