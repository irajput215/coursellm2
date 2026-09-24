"""Integration tests for the recursive-CTE graph queries, against real PostgreSQL.

The owner role creates the fixture (it bypasses Row-Level Security, as a fixture
must); every query runs through :func:`~coursellm.db.tenancy.tenant_session` as
the restricted application role, so a missing policy would make these tests fail
rather than pass for the wrong reason.

The fixture is data, not schema, so closure correctness is asserted against a
hand-computed expected set. The cyclic fixture is inserted deliberately (the
self-loop CHECK does not prevent a two-node cycle) and proves the read-time path
guard terminates the traversal: the test is wrapped in ``asyncio.wait_for`` so an
unbounded CTE fails the test instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.graph.repository import ConceptGraphRepository
from coursellm.tools.graph_tools import SearchKnowledgeGraphArgs, make_handler
from coursellm.tools.registry import ToolContext
from tests.integration.conftest import Seeded

pytestmark = pytest.mark.integration

# Generous but bounded: the guard must terminate long before this.
CYCLE_TIMEOUT_SECONDS = 10.0


async def _insert_concept(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    slug: str,
    name: str | None = None,
    difficulty: int = 3,
    verified: bool = False,
) -> uuid.UUID:
    concept_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO concepts "
                "(id, tenant_id, course_id, name, slug, difficulty, confidence, verified) "
                "VALUES (:id, :tenant_id, :course_id, :name, :slug, :difficulty, 0.9, :verified)"
            ),
            {
                "id": str(concept_id),
                "tenant_id": str(tenant_id),
                "course_id": str(course_id),
                "name": name or slug.replace("-", " ").title(),
                "slug": slug,
                "difficulty": difficulty,
                "verified": verified,
            },
        )
    return concept_id


async def _insert_edge(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation: str = "requires",
    confidence: float = 0.9,
    weight: float = 1.0,
    verified: bool = False,
    cue: str = "explicit",
) -> uuid.UUID:
    edge_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO concept_edges "
                "(id, tenant_id, source_concept_id, target_concept_id, relation, weight, "
                " confidence, corroboration_count, cue, verified) "
                "VALUES (:id, :tenant_id, :source, :target, :relation, :weight, "
                " :confidence, 2, :cue, :verified)"
            ),
            {
                "id": str(edge_id),
                "tenant_id": str(tenant_id),
                "source": str(source_id),
                "target": str(target_id),
                "relation": relation,
                "weight": weight,
                "confidence": confidence,
                "cue": cue,
                "verified": verified,
            },
        )
    return edge_id


class _Dag:
    """The seeded DAG's identifiers, keyed by slug."""

    def __init__(self, ids: dict[str, uuid.UUID]) -> None:
        self.ids = ids

    def __getitem__(self, slug: str) -> uuid.UUID:
        return self.ids[slug]


async def _seed_dag(
    owner_engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    prefix: str = "",
) -> _Dag:
    """Seed the fixture DAG plus a ``related_to`` cross-edge and a sibling pair."""
    names = [
        "linear-algebra",
        "probability",
        "optimization",
        "classical-ml",
        "deep-learning",
        "transformers",
        "attention",
        "multi-head-attention",
        "positional-encoding",
    ]
    ids = {
        name: await _insert_concept(
            owner_engine,
            tenant_id=tenant_id,
            course_id=course_id,
            slug=f"{prefix}{name}",
        )
        for name in names
    }
    requires = [
        ("classical-ml", "linear-algebra"),
        ("classical-ml", "probability"),
        ("deep-learning", "classical-ml"),
        ("deep-learning", "optimization"),
        ("transformers", "deep-learning"),
    ]
    for source, target in requires:
        await _insert_edge(
            owner_engine,
            tenant_id=tenant_id,
            source_id=ids[source],
            target_id=ids[target],
        )
    # A non-transitive association that must never appear in a closure.
    await _insert_edge(
        owner_engine,
        tenant_id=tenant_id,
        source_id=ids["transformers"],
        target_id=ids["attention"],
        relation="related_to",
        cue="inferred",
    )
    # A part_of hierarchy with two siblings.
    await _insert_edge(
        owner_engine,
        tenant_id=tenant_id,
        source_id=ids["multi-head-attention"],
        target_id=ids["attention"],
        relation="part_of",
    )
    await _insert_edge(
        owner_engine,
        tenant_id=tenant_id,
        source_id=ids["positional-encoding"],
        target_id=ids["attention"],
        relation="part_of",
    )
    return _Dag(ids)


async def test_prerequisite_closure_returns_depths_and_paths(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        nodes = await repo.prerequisite_closure(
            dag["transformers"], max_depth=3, min_confidence=0.6
        )

    by_slug = {node.slug: node for node in nodes}
    assert set(by_slug) == {
        "deep-learning",
        "classical-ml",
        "optimization",
        "linear-algebra",
        "probability",
    }
    assert by_slug["deep-learning"].depth == 1
    assert by_slug["classical-ml"].depth == 2
    assert by_slug["optimization"].depth == 2
    assert by_slug["linear-algebra"].depth == 3
    assert by_slug["probability"].depth == 3

    for node in nodes:
        assert node.path[0] == dag["transformers"]
        assert node.path[-1] == node.concept_id
    # ``related_to`` is never traversed for a closure.
    assert "attention" not in by_slug


async def test_depth_limit_returns_only_direct_prerequisites(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        nodes = await repo.prerequisite_closure(
            dag["transformers"], max_depth=1, min_confidence=0.6
        )
    assert [node.slug for node in nodes] == ["deep-learning"]
    assert all(node.depth == 1 for node in nodes)


async def test_min_confidence_excludes_a_weak_edge(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    weak = await _insert_concept(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.course_a_id,
        slug="weak-topic",
    )
    await _insert_edge(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        source_id=dag["transformers"],
        target_id=weak,
        confidence=0.2,
    )
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        filtered = await repo.prerequisite_closure(
            dag["transformers"], max_depth=3, min_confidence=0.6
        )
        unrestricted = await repo.prerequisite_closure(
            dag["transformers"], max_depth=3, min_confidence=0.0
        )
    assert "weak-topic" not in {node.slug for node in filtered}
    assert "weak-topic" in {node.slug for node in unrestricted}


async def test_direct_prerequisites_order_verified_first(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        direct = await repo.direct_prerequisites(dag["transformers"], min_confidence=0.6)
    assert [item.slug for item in direct] == ["deep-learning"]
    assert direct[0].cue == "explicit"


async def test_related_concepts_includes_associations_and_siblings(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        associations = await repo.related_concepts(dag["transformers"], min_confidence=0.6)
        siblings = await repo.related_concepts(dag["multi-head-attention"], min_confidence=0.6)
    assert [item.slug for item in associations] == ["attention"]
    assert associations[0].kind == "related_to"
    assert {item.slug for item in siblings} == {"positional-encoding"}
    assert siblings[0].kind == "sibling"


async def test_knowledge_gap_reports_required_but_unmastered(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    mastery = {
        str(dag["linear-algebra"]): 0.9,
        str(dag["probability"]): 0.1,
        str(dag["optimization"]): 0.95,
    }
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        gaps = await repo.knowledge_gap(
            mastery, dag["transformers"], max_depth=3, min_confidence=0.6
        )
    by_slug = {gap.slug: gap for gap in gaps}
    assert set(by_slug) == {"classical-ml", "deep-learning", "probability"}
    assert by_slug["probability"].mastery == pytest.approx(0.1)
    assert by_slug["classical-ml"].never_assessed is True
    assert by_slug["deep-learning"].never_assessed is True
    # Deepest first: the most immediate gap to close.
    assert gaps[0].depth >= gaps[-1].depth


async def test_detect_cycles_finds_the_injected_cycle(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        assert await repo.detect_cycles() == []
    # Inject the cycle after proving the healthy fixture is acyclic.
    await _insert_edge(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        source_id=dag["deep-learning"],
        target_id=dag["transformers"],
    )
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        cycles = await repo.detect_cycles()
    assert cycles, "the injected cycle was not detected"
    assert any(
        dag["transformers"] in cycle.path and dag["deep-learning"] in cycle.path for cycle in cycles
    )


async def test_cyclic_graph_terminates_within_a_timeout(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    """An unbounded recursive CTE would hang the whole suite; the guard prevents it."""
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    await _insert_edge(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        source_id=dag["deep-learning"],
        target_id=dag["transformers"],
    )
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        nodes = await asyncio.wait_for(
            repo.prerequisite_closure(dag["transformers"], max_depth=10, min_confidence=0.6),
            timeout=CYCLE_TIMEOUT_SECONDS,
        )
    ids = [node.concept_id for node in nodes]
    assert len(ids) == len(set(ids)), "the closure returned the same concept twice"
    assert dag["transformers"] not in ids, "the cycle was not guarded"
    assert "deep-learning" in {node.slug for node in nodes}


async def test_plan_roadmap_is_topological_and_deterministic(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    mastered = {str(dag["linear-algebra"]): 1.0, str(dag["probability"]): 1.0}
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        first = await repo.plan_roadmap(dag["transformers"], mastered, min_confidence=0.6)
        second = await repo.plan_roadmap(dag["transformers"], mastered, min_confidence=0.6)
    assert [step.slug for step in first.steps] == [
        "classical-ml",
        "optimization",
        "deep-learning",
        "transformers",
    ]
    assert first == second, "the plan is not deterministic"
    assert first.unmet_cycle == ()
    # A mastered concept is not rescheduled.
    assert "linear-algebra" not in {step.slug for step in first.steps}


async def test_plan_roadmap_reports_a_cycle_instead_of_looping(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    await _insert_edge(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        source_id=dag["deep-learning"],
        target_id=dag["transformers"],
    )
    mastered = {str(dag["linear-algebra"]): 1.0, str(dag["probability"]): 1.0}
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        plan = await asyncio.wait_for(
            repo.plan_roadmap(dag["transformers"], mastered, min_confidence=0.6),
            timeout=CYCLE_TIMEOUT_SECONDS,
        )
    assert plan.unmet_cycle, "the cycle was silently dropped"
    assert plan.steps, "a partial order should still be emitted"


async def test_tenant_isolation_across_graph_queries(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    tenant_a_dag = await _seed_dag(
        owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id
    )
    tenant_b_dag = await _seed_dag(
        owner_engine,
        tenant_id=seeded.tenant_b_id,
        course_id=seeded.course_b_id,
        prefix="b-",
    )
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repo = ConceptGraphRepository(session, TenantScope(seeded.tenant_a_id))
        nodes = await repo.prerequisite_closure(
            tenant_a_dag["transformers"], max_depth=3, min_confidence=0.6
        )
    returned = {node.concept_id for node in nodes}
    assert returned, "the queried tenant's closure was empty"
    assert tenant_b_dag["transformers"] not in returned
    assert not any(node.slug.startswith("b-") for node in nodes)
    # Tenant B's own closure is non-empty and disjoint, proving both were seeded.
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_b_id)) as session:
        repo_b = ConceptGraphRepository(session, TenantScope(seeded.tenant_b_id))
        nodes_b = await repo_b.prerequisite_closure(
            tenant_b_dag["transformers"], max_depth=3, min_confidence=0.6
        )
    assert {node.concept_id for node in nodes_b}.isdisjoint(returned)


async def test_tool_handler_returns_typed_closure_with_provenance(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        context = ToolContext(
            tenant_id=seeded.tenant_a_id,
            user_id=seeded.user_a_id,
            settings=pg_settings,
            session=session,
        )
        handler = make_handler()
        result = await handler(
            SearchKnowledgeGraphArgs(concept_name="Transformers", max_depth=3), context
        )

    assert result.entities
    slugs = {entity["slug"] for entity in result.entities}
    assert slugs == {
        "deep-learning",
        "classical-ml",
        "optimization",
        "linear-algebra",
        "probability",
    }
    first = result.entities[0]
    assert {
        "concept_id",
        "name",
        "slug",
        "relation",
        "depth",
        "direction",
        "weight",
        "confidence",
        "verified",
        "path",
        "provenance",
    } <= set(first)
    assert first["direction"] == "prerequisite_of"
    assert first["path"][0] == str(dag["transformers"])
    assert result.depth_used == 3


async def test_tool_handler_never_traverses_related_to_as_a_closure(
    pg_settings: Settings, seeded: Seeded, owner_engine: AsyncEngine
) -> None:
    dag = await _seed_dag(owner_engine, tenant_id=seeded.tenant_a_id, course_id=seeded.course_a_id)
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        context = ToolContext(
            tenant_id=seeded.tenant_a_id,
            user_id=seeded.user_a_id,
            settings=pg_settings,
            session=session,
        )
        handler = make_handler()
        related = await handler(
            SearchKnowledgeGraphArgs(concept_id=dag["transformers"], relation="related_to"),
            context,
        )
        dependents = await handler(
            SearchKnowledgeGraphArgs(concept_id=dag["linear-algebra"], direction="dependents"),
            context,
        )
    assert [entity["slug"] for entity in related.entities] == ["attention"]
    assert all(entity["depth"] == 1 for entity in related.entities)
    dependent_slugs = {entity["slug"] for entity in dependents.entities}
    assert {"classical-ml", "deep-learning", "transformers"} <= dependent_slugs
