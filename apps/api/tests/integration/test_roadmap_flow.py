"""End-to-end roadmap flow against real PostgreSQL with the RLS-enforcing role.

The graph fixture is the one from ``knowledge-graph.md`` §11.1, seeded through the
owner engine (a fixture must bypass RLS); every request runs as
``coursellm_app``, so a missing policy or a missing tenant filter fails the test
rather than passing for the wrong reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from tests.integration.conftest import SEED_PASSWORD, Seeded

pytestmark = pytest.mark.integration

API = "/api/v1"
DAG_SLUGS = (
    "linear-algebra",
    "probability",
    "optimization",
    "classical-ml",
    "deep-learning",
    "transformers",
)
REQUIRES = (
    ("classical-ml", "linear-algebra"),
    ("classical-ml", "probability"),
    ("deep-learning", "classical-ml"),
    ("deep-learning", "optimization"),
    ("transformers", "deep-learning"),
)
DIFFICULTY = {
    "linear-algebra": 2,
    "probability": 2,
    "optimization": 3,
    "classical-ml": 3,
    "deep-learning": 4,
    "transformers": 5,
}


async def _insert_concept(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    slug: str,
    difficulty: int = 3,
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
                "name": slug.replace("-", " ").title(),
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
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO concept_edges "
                "(id, tenant_id, source_concept_id, target_concept_id, relation, weight, "
                " confidence, corroboration_count, cue, verified) "
                "VALUES (:id, :tenant_id, :source, :target, 'requires', 1.0, "
                " 0.9, 2, 'explicit', false)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "source": str(source_id),
                "target": str(target_id),
            },
        )


async def _seed_dag(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    prefix: str = "",
) -> dict[str, uuid.UUID]:
    ids = {
        slug: await _insert_concept(
            engine,
            tenant_id=tenant_id,
            course_id=course_id,
            slug=f"{prefix}{slug}",
            difficulty=DIFFICULTY[slug],
        )
        for slug in DAG_SLUGS
    }
    for source, target in REQUIRES:
        await _insert_edge(
            engine,
            tenant_id=tenant_id,
            source_id=ids[source],
            target_id=ids[target],
        )
    return ids


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_roadmap(
    client: AsyncClient, headers: dict[str, str], course_id: uuid.UUID
) -> dict[str, object]:
    response = await client.post(
        f"{API}/roadmaps",
        json={"course_id": str(course_id), "goal_text": "transformers"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _step(steps: list[dict[str, object]], title: str) -> dict[str, object]:
    matches = [step for step in steps if step["title"] == title]
    assert matches, f"no step titled {title!r} in {[s['title'] for s in steps]}"
    return matches[0]


async def _event_count(engine: AsyncEngine, *, tenant_id: uuid.UUID, kind: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM progress_events "
                        "WHERE tenant_id = :tenant_id AND kind = :kind"
                    ),
                    {"tenant_id": str(tenant_id), "kind": kind},
                )
            ).scalar_one()
        )


async def test_create_roadmap_orders_steps_topologically(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    headers = await _login(api_client, seeded.tenant_a.email)

    body = await _create_roadmap(api_client, headers, seeded.course_a_id)
    steps = body["steps"]
    assert isinstance(steps, list)
    assert [step["title"] for step in steps] == [
        "Linear Algebra",
        "Probability",
        "Classical Ml",
        "Optimization",
        "Deep Learning",
        "Transformers",
    ]
    assert steps[0]["status"] == "available"
    assert all(step["status"] in {"pending", "blocked"} for step in steps[1:])

    classical = _step(steps, "Classical Ml")
    assert set(classical["blocked_by"]) == {
        str(dag["linear-algebra"]),
        str(dag["probability"]),
    }
    deep = _step(steps, "Deep Learning")
    assert set(deep["blocked_by"]) == {str(dag["classical-ml"]), str(dag["optimization"])}
    # Effort is present and sourced, not zero-filled.
    assert all(float(step["estimated_hours"]) > 0 for step in steps)


async def test_reposting_an_unchanged_goal_reuses_the_revision(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    headers = await _login(api_client, seeded.tenant_a.email)

    first = await _create_roadmap(api_client, headers, seeded.course_a_id)
    second = await api_client.post(
        f"{API}/roadmaps",
        json={"course_id": str(seeded.course_a_id), "goal_text": "transformers"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first["id"]
    assert second.json()["revision"] == 1

    listed = await api_client.get(f"{API}/roadmaps", headers=headers)
    assert len(listed.json()) == 1


async def test_completing_a_step_writes_an_event_and_unblocks_dependents(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _create_roadmap(api_client, headers, seeded.course_a_id)
    roadmap_id = body["id"]
    steps = body["steps"]
    linear = _step(steps, "Linear Algebra")
    probability = _step(steps, "Probability")

    before = (await api_client.get(f"{API}/progress", headers=headers)).json()
    assert before["mastery"] == {}

    completed = await api_client.patch(
        f"{API}/roadmaps/{roadmap_id}/steps/{linear['id']}",
        json={"status": "completed"},
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert (
        await _event_count(
            owner_engine, tenant_id=seeded.tenant_a_id, kind="roadmap_step_completed"
        )
        == 1
    )

    after = (await api_client.get(f"{API}/progress", headers=headers)).json()
    assert float(after["mastery"][str(linear["concept_id"])]) == pytest.approx(1.0)

    # Idempotent: completing again writes no second event.
    again = await api_client.patch(
        f"{API}/roadmaps/{roadmap_id}/steps/{linear['id']}",
        json={"status": "completed"},
        headers=headers,
    )
    assert again.status_code == 200
    assert (
        await _event_count(
            owner_engine, tenant_id=seeded.tenant_a_id, kind="roadmap_step_completed"
        )
        == 1
    )

    # Completing the last unsatisfied prerequisite makes the dependent available.
    second = await api_client.patch(
        f"{API}/roadmaps/{roadmap_id}/steps/{probability['id']}",
        json={"status": "completed"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    updated = {step["title"]: step for step in second.json()["steps"]}
    assert updated["Classical Ml"]["status"] == "available"
    assert updated["Classical Ml"]["blocked_by"] == []


async def test_completing_a_blocked_step_is_a_typed_conflict(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _create_roadmap(api_client, headers, seeded.course_a_id)
    roadmap_id = body["id"]
    classical = _step(body["steps"], "Classical Ml")

    response = await api_client.patch(
        f"{API}/roadmaps/{roadmap_id}/steps/{classical['id']}",
        json={"status": "completed"},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "step_blocked"
    assert (
        await _event_count(
            owner_engine, tenant_id=seeded.tenant_a_id, kind="roadmap_step_completed"
        )
        == 0
    )


async def test_adapt_revises_and_preserves_completed_steps(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    headers = await _login(api_client, seeded.tenant_a.email)
    body = await _create_roadmap(api_client, headers, seeded.course_a_id)
    roadmap_id = body["id"]
    linear = _step(body["steps"], "Linear Algebra")

    completed = await api_client.patch(
        f"{API}/roadmaps/{roadmap_id}/steps/{linear['id']}",
        json={"status": "completed"},
        headers=headers,
    )
    assert completed.status_code == 200, completed.text

    # Mark probability mastered directly: PR 13 owns the assessment path that
    # would normally write this event.
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO progress_events "
                "(id, tenant_id, user_id, course_id, concept_id, kind, mastery, weight, "
                " occurred_at, source) "
                "VALUES (:id, :tenant_id, :user_id, :course_id, :concept_id, "
                " 'concept_mastered', 1.0, 1.0, :occurred_at, 'assessment')"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(seeded.tenant_a_id),
                "user_id": str(seeded.user_a_id),
                "course_id": str(seeded.course_a_id),
                "concept_id": str(dag["probability"]),
                "occurred_at": datetime.now(UTC),
            },
        )

    adapted = await api_client.post(
        f"{API}/roadmaps/{roadmap_id}/adapt",
        json={"reason": "Probability mastered after a quiz"},
        headers=headers,
    )
    assert adapted.status_code == 200, adapted.text
    revised = adapted.json()
    assert revised["revision"] == 2
    titles = [step["title"] for step in revised["steps"]]
    assert "Probability" not in titles
    assert "Linear Algebra" in titles
    preserved = _step(revised["steps"], "Linear Algebra")
    assert preserved["status"] == "completed"
    assert _step(revised["steps"], "Classical Ml")["status"] == "available"

    # The previous revision is superseded, not deleted; the list shows the newest.
    async with owner_engine.connect() as connection:
        old_status = (
            await connection.execute(
                text("SELECT status FROM roadmaps WHERE id = :id"),
                {"id": str(roadmap_id)},
            )
        ).scalar_one()
    assert old_status == "superseded"

    listed = await api_client.get(f"{API}/roadmaps", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["revision"] == 2

    # A second adaptation with nothing new is a no-op: no third revision.
    noop = await api_client.post(
        f"{API}/roadmaps/{revised['id']}/adapt",
        json={"reason": "Nothing changed"},
        headers=headers,
    )
    assert noop.status_code == 200, noop.text
    assert noop.json()["revision"] == 2


async def test_two_tenants_cannot_see_each_others_roadmaps_or_progress(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    await _seed_dag(
        owner_engine,
        tenant_id=seeded.tenant_b_id,
        course_id=seeded.course_b_id,
        prefix="b-",
    )
    headers_a = await _login(api_client, seeded.tenant_a.email)
    headers_b = await _login(api_client, seeded.tenant_b.email)

    created = await _create_roadmap(api_client, headers_a, seeded.course_a_id)

    # Tenant B cannot read A's roadmap, by id or by list.
    cross = await api_client.get(f"{API}/roadmaps/{created['id']}", headers=headers_b)
    assert cross.status_code == 404
    listed_b = await api_client.get(f"{API}/roadmaps", headers=headers_b)
    assert listed_b.status_code == 200
    assert listed_b.json() == []

    # Tenant B's progress is its own: A's stored evidence is invisible.
    progress_b = await api_client.get(f"{API}/progress", headers=headers_b)
    assert progress_b.status_code == 200
    assert progress_b.json()["mastery"] == {}

    # Both tenants can create their own plan, and neither list contains the other.
    created_b = await api_client.post(
        f"{API}/roadmaps",
        json={"course_id": str(seeded.course_b_id), "goal_text": "transformers"},
        headers=headers_b,
    )
    assert created_b.status_code == 201, created_b.text
    listed_a = await api_client.get(f"{API}/roadmaps", headers=headers_a)
    assert [row["id"] for row in listed_a.json()] == [created["id"]]
    assert all(row["id"] != created_b.json()["id"] for row in listed_a.json())
