"""End-to-end recommendation flow against real PostgreSQL with the RLS-enforcing role.

The catalogue is seeded through the admin endpoint (which is itself under test),
and every recommendation is checked against the database: a returned resource must
exist in ``resources`` and must cover at least one reported gap. That is the
regression guard for the hard rule of this PR — a fabricated or unrelated
recommendation fails here.

``resources`` and ``resource_concepts`` are global, so the shared integration
fixture does not truncate them. This module truncates them itself, through the
owner engine, before and after every test.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.recommend.seed import SEED_CATALOGUE
from coursellm.security.passwords import hash_password
from tests.integration.conftest import SEED_PASSWORD, Seeded

pytestmark = pytest.mark.integration

API = "/api/v1"

#: A prerequisite DAG whose slugs are all covered by the seeded catalogue.
ML_DAG: tuple[str, ...] = (
    "linear-algebra",
    "probability",
    "optimization",
    "gradient-descent",
    "machine-learning",
    "deep-learning",
    "transformers",
)
ML_REQUIRES: tuple[tuple[str, str], ...] = (
    ("machine-learning", "linear-algebra"),
    ("machine-learning", "probability"),
    ("deep-learning", "machine-learning"),
    ("deep-learning", "optimization"),
    ("deep-learning", "gradient-descent"),
    ("transformers", "deep-learning"),
)
ML_DIFFICULTY = {
    "linear-algebra": 2,
    "probability": 2,
    "optimization": 3,
    "gradient-descent": 3,
    "machine-learning": 3,
    "deep-learning": 4,
    "transformers": 5,
}

#: A DAG whose slugs are deliberately absent from the catalogue.
UNCOVERED_DAG: tuple[str, ...] = ("quantum-chemistry", "molecular-orbital-theory")


@pytest_asyncio.fixture(loop_scope="function", autouse=True)
async def clean_catalogue(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    """Truncate the global catalogue around each test.

    The shared ``clean_db`` fixture truncates tenants and tenant-scoped tables
    only; the catalogue is global by design, so this module owns its cleanup.
    """
    statement = "TRUNCATE TABLE resource_concepts, resources RESTART IDENTITY CASCADE"
    async with owner_engine.begin() as connection:
        await connection.execute(text(statement))
    yield
    async with owner_engine.begin() as connection:
        await connection.execute(text(statement))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _insert_user(
    engine: AsyncEngine, *, tenant_id: uuid.UUID, email: str, role: str
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, email, hashed_password, full_name, role, is_active) "
                "VALUES (:id, :tenant_id, :email, :hashed, :full_name, :role, true)"
            ),
            {
                "id": str(user_id),
                "tenant_id": str(tenant_id),
                "email": email,
                "hashed": hash_password(SEED_PASSWORD),
                "full_name": "Member User",
                "role": role,
            },
        )
    return user_id


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


async def _seed_graph(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    slugs: tuple[str, ...],
    requires: tuple[tuple[str, str], ...],
    difficulty: dict[str, int] | None = None,
) -> dict[str, uuid.UUID]:
    levels = difficulty or {}
    ids = {
        slug: await _insert_concept(
            engine,
            tenant_id=tenant_id,
            course_id=course_id,
            slug=slug,
            difficulty=levels.get(slug, 3),
        )
        for slug in slugs
    }
    for source, target in requires:
        await _insert_edge(
            engine, tenant_id=tenant_id, source_id=ids[source], target_id=ids[target]
        )
    return ids


async def _insert_mastery_event(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    concept_id: uuid.UUID,
    mastery: float,
    kind: str = "concept_mastered",
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO progress_events "
                "(id, tenant_id, user_id, course_id, concept_id, kind, mastery, weight, "
                " occurred_at, source) "
                "VALUES (:id, :tenant_id, :user_id, :course_id, :concept_id, :kind, "
                " :mastery, 1.0, now(), 'assessment')"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "course_id": str(course_id),
                "concept_id": str(concept_id),
                "kind": kind,
                "mastery": mastery,
            },
        )


async def _seed_catalogue(client: AsyncClient, headers: dict[str, str]) -> dict[str, int]:
    response = await client.post(f"{API}/recommendations/seed", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _create_roadmap(
    client: AsyncClient, headers: dict[str, str], course_id: uuid.UUID, goal_text: str
) -> dict[str, object]:
    response = await client.post(
        f"{API}/roadmaps",
        json={"course_id": str(course_id), "goal_text": goal_text},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _recommendations(
    client: AsyncClient, headers: dict[str, str], course_id: uuid.UUID, **params: object
) -> dict[str, object]:
    response = await client.get(
        f"{API}/recommendations",
        params={"course_id": str(course_id), **params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _resource_urls(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT id, url FROM resources"))).all()
    return {str(row[0]): str(row[1]) for row in rows}


def _gap_slugs(body: dict[str, object]) -> set[str]:
    gaps = body["gaps"]
    assert isinstance(gaps, list)
    return {str(gap["slug"]) for gap in gaps}


def _recommendation_slugs(body: dict[str, object]) -> list[set[str]]:
    items = body["recommendations"]
    assert isinstance(items, list)
    return [{str(slug) for slug in item["covered_gap_slugs"]} for item in items]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_seeded_catalogue_recommends_real_resources_for_a_mastery_gap(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    headers = await _login(api_client, seeded.tenant_a.email)
    report = await _seed_catalogue(api_client, headers)
    assert report["inserted"] == len(SEED_CATALOGUE)

    dag = await _seed_graph(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slugs=ML_DAG,
        requires=ML_REQUIRES,
        difficulty=ML_DIFFICULTY,
    )
    # A measured mastery gap: linear algebra is known, so the roadmap omits it.
    await _insert_mastery_event(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        user_id=seeded.user_a_id,
        course_id=seeded.course_a_id,
        concept_id=dag["linear-algebra"],
        mastery=1.0,
    )
    await _create_roadmap(api_client, headers, seeded.course_a_id, "transformers")

    body = await _recommendations(api_client, headers, seeded.course_a_id, limit=10)

    assert body["personalised"] is True
    gaps = _gap_slugs(body)
    assert "linear-algebra" not in gaps
    assert "transformers" in gaps
    assert body["degraded"] == [] or "no_catalogue_coverage" not in body["degraded"]

    catalogue_urls = await _resource_urls(owner_engine)
    items = body["recommendations"]
    assert isinstance(items, list)
    assert items, "the seeded catalogue must be able to cover this DAG"
    for item in items:
        assert item["resource"]["id"] in catalogue_urls
        assert item["resource"]["url"] in set(catalogue_urls.values())
        covered = {str(slug) for slug in item["covered_gap_slugs"]}
        assert covered, f"{item['resource']['title']!r} covers no reported gap"
        assert covered <= gaps, "a recommendation claimed a gap that was not reported"
        explanation = item["explanation"]
        assert set(explanation["covered_gap_slugs"]) <= gaps
        assert explanation["primary_gap_slug"] in gaps
        assert explanation["primary_gap_name"] in explanation["summary"]


async def test_gap_detection_falls_back_to_mastery_without_a_roadmap(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    headers = await _login(api_client, seeded.tenant_a.email)
    await _seed_catalogue(api_client, headers)
    dag = await _seed_graph(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slugs=ML_DAG,
        requires=ML_REQUIRES,
        difficulty=ML_DIFFICULTY,
    )
    # No roadmap. Weak evidence on the goal surfaces its unmet prerequisites.
    await _insert_mastery_event(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        user_id=seeded.user_a_id,
        course_id=seeded.course_a_id,
        concept_id=dag["deep-learning"],
        mastery=0.2,
        kind="concept_struggled",
    )

    body = await _recommendations(api_client, headers, seeded.course_a_id, limit=10)

    assert body["personalised"] is True
    gaps = _gap_slugs(body)
    assert "linear-algebra" in gaps
    assert "machine-learning" in gaps
    assert body["recommendations"], "catalogue resources cover these prerequisites"
    for covered in _recommendation_slugs(body):
        assert covered & gaps


async def test_reseed_is_idempotent_and_never_overwrites_metadata(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    headers = await _login(api_client, seeded.tenant_a.email)
    first = await _seed_catalogue(api_client, headers)
    assert first["inserted"] == len(SEED_CATALOGUE)
    assert first["skipped"] == 0

    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT id, description FROM resources ORDER BY url LIMIT 1")
            )
        ).one()
    resource_id, original_description = str(row[0]), str(row[1])

    # A reviewer edits the row directly; re-seeding must not clobber it.
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE resources SET description = :description WHERE id = :id"),
            {"description": "Edited by a reviewer.", "id": resource_id},
        )

    second = await _seed_catalogue(api_client, headers)
    assert second["inserted"] == 0
    assert second["skipped"] == len(SEED_CATALOGUE)

    async with owner_engine.connect() as connection:
        description = (
            await connection.execute(
                text("SELECT description FROM resources WHERE id = :id"),
                {"id": resource_id},
            )
        ).scalar_one()
        total = (await connection.execute(text("SELECT count(*) FROM resources"))).scalar_one()
    assert description == "Edited by a reviewer."
    assert original_description != description
    assert int(total) == len(SEED_CATALOGUE)


async def test_catalogue_search_filters_by_type_trust_and_text(
    pg_settings: Settings,
    seeded: Seeded,
    api_client: AsyncClient,
) -> None:
    headers = await _login(api_client, seeded.tenant_a.email)
    await _seed_catalogue(api_client, headers)

    books = await api_client.get(
        f"{API}/recommendations/resources",
        params={"resource_type": "book", "limit": 100},
        headers=headers,
    )
    assert books.status_code == 200, books.text
    book_body = books.json()
    assert book_body["total"] >= 1
    assert all(item["resource_type"] == "book" for item in book_body["items"])

    official = await api_client.get(
        f"{API}/recommendations/resources",
        params={"trust": "official", "limit": 100},
        headers=headers,
    )
    assert official.status_code == 200, official.text
    official_body = official.json()
    assert official_body["total"] >= 1
    assert all(item["trust"] == "official" for item in official_body["items"])

    textual = await api_client.get(
        f"{API}/recommendations/resources",
        params={"q": "transformers", "limit": 100},
        headers=headers,
    )
    assert textual.status_code == 200, textual.text
    text_body = textual.json()
    assert text_body["total"] >= 1
    for item in text_body["items"]:
        haystack = " ".join(
            str(item[field]) for field in ("title", "description", "provider")
        ).lower()
        assert "transformers" in haystack

    # Pagination is disjoint and reports the same unpaginated total.
    first_page = await api_client.get(
        f"{API}/recommendations/resources",
        params={"limit": 5, "offset": 0},
        headers=headers,
    )
    second_page = await api_client.get(
        f"{API}/recommendations/resources",
        params={"limit": 5, "offset": 5},
        headers=headers,
    )
    first_ids = {item["id"] for item in first_page.json()["items"]}
    second_ids = {item["id"] for item in second_page.json()["items"]}
    assert len(first_ids) == 5
    assert not (first_ids & second_ids)
    assert first_page.json()["total"] == second_page.json()["total"] == len(SEED_CATALOGUE)

    # One resource, with its concept coverage.
    resource_id = next(iter(first_ids))
    detail = await api_client.get(f"{API}/recommendations/{resource_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == resource_id
    assert detail.json()["concept_slugs"]


async def test_seed_endpoint_requires_an_admin_role(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    owner_headers = await _login(api_client, seeded.tenant_a.email)
    await _seed_catalogue(api_client, owner_headers)

    await _insert_user(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        email="member@example.com",
        role="member",
    )
    member_headers = await _login(api_client, "member@example.com")

    forbidden = await api_client.post(f"{API}/recommendations/seed", headers=member_headers)
    assert forbidden.status_code == 403, forbidden.text

    # Reads are still available to the member; only the global write is gated.
    allowed = await api_client.get(f"{API}/recommendations/resources", headers=member_headers)
    assert allowed.status_code == 200

    again = await api_client.post(f"{API}/recommendations/seed", headers=owner_headers)
    assert again.status_code == 200, again.text
    assert again.json()["inserted"] == 0


async def test_gap_without_catalogue_coverage_is_reported_not_filled(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    headers = await _login(api_client, seeded.tenant_a.email)
    await _seed_catalogue(api_client, headers)
    await _seed_graph(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slugs=UNCOVERED_DAG,
        requires=(("quantum-chemistry", "molecular-orbital-theory"),),
    )
    await _create_roadmap(api_client, headers, seeded.course_a_id, "quantum-chemistry")

    body = await _recommendations(api_client, headers, seeded.course_a_id, limit=10)

    degraded = body["degraded"]
    assert "no_catalogue_coverage" in degraded
    assert "no_catalogue_coverage:quantum-chemistry" in degraded
    assert body["recommendations"] == [], (
        "a gap with no coverage must not be filled with an unrelated resource"
    )


async def test_unauthenticated_requests_are_rejected(
    pg_settings: Settings,
    seeded: Seeded,
    api_client: AsyncClient,
) -> None:
    for method, path in (
        ("get", f"{API}/recommendations"),
        ("get", f"{API}/recommendations/resources"),
        ("post", f"{API}/recommendations/seed"),
    ):
        response = await getattr(api_client, method)(path)
        assert response.status_code == 401, (method, path, response.text)


async def test_one_users_gaps_never_appear_in_another_users_response(
    pg_settings: Settings,
    seeded: Seeded,
    owner_engine: AsyncEngine,
    api_client: AsyncClient,
) -> None:
    headers_a = await _login(api_client, seeded.tenant_a.email)
    await _seed_catalogue(api_client, headers_a)
    await _seed_graph(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slugs=ML_DAG,
        requires=ML_REQUIRES,
        difficulty=ML_DIFFICULTY,
    )
    await _seed_graph(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slugs=("sql", "database-indexing", "query-optimization"),
        requires=(
            ("query-optimization", "database-indexing"),
            ("database-indexing", "sql"),
        ),
    )
    await _insert_user(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        email="second@example.com",
        role="owner",
    )
    headers_b = await _login(api_client, "second@example.com")

    await _create_roadmap(api_client, headers_a, seeded.course_a_id, "transformers")
    await _create_roadmap(api_client, headers_b, seeded.course_a_id, "query-optimization")

    body_a = await _recommendations(api_client, headers_a, seeded.course_a_id, limit=10)
    body_b = await _recommendations(api_client, headers_b, seeded.course_a_id, limit=10)

    gaps_a = _gap_slugs(body_a)
    gaps_b = _gap_slugs(body_b)
    assert gaps_a, "user A must have gaps from their own roadmap"
    assert gaps_b, "user B must have gaps from their own roadmap"
    assert gaps_a.isdisjoint(gaps_b), (
        f"one user's gaps leaked into another's response: {gaps_a & gaps_b}"
    )
    resource_ids_a = {item["resource"]["id"] for item in body_a["recommendations"]}
    resource_ids_b = {item["resource"]["id"] for item in body_b["recommendations"]}
    assert resource_ids_a
    assert resource_ids_b
    assert resource_ids_a != resource_ids_b
