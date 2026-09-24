"""Unit tests for the roadmap planner's pure core.

No database: ``build_plan`` is fed the same dataclasses the repository returns, so
ordering, mastery subtraction, blocking and effort are asserted against
hand-computed expectations rather than against whatever a query happens to
return.
"""

from __future__ import annotations

import uuid

import pytest

from coursellm.db.models.graph import Concept
from coursellm.db.models.learning import RoadmapStepStatus
from coursellm.graph.repository import ClosureEdge, ClosureNode, KnowledgeGap
from coursellm.learning.planner import (
    BASE_HOURS_BY_DIFFICULTY,
    DEGRADED_STEP_HOURS,
    MAX_STEP_HOURS,
    build_plan,
    degraded_plan,
    estimate_step,
    estimate_step_hours,
    topological_order,
)
from coursellm.learning.schemas import GoalUnresolvedError, RoadmapCycleError

pytestmark = pytest.mark.unit

# The fixture DAG, mirroring docs/architecture/knowledge-graph.md §11.1.
SLUGS = (
    "linear-algebra",
    "probability",
    "optimization",
    "classical-ml",
    "deep-learning",
    "transformers",
)
DIFFICULTY = {
    "linear-algebra": 2,
    "probability": 2,
    "optimization": 3,
    "classical-ml": 3,
    "deep-learning": 4,
    "transformers": 5,
}
REQUIRES = (
    ("classical-ml", "linear-algebra"),
    ("classical-ml", "probability"),
    ("deep-learning", "classical-ml"),
    ("deep-learning", "optimization"),
    ("transformers", "deep-learning"),
)


def _ids() -> dict[str, uuid.UUID]:
    return {slug: uuid.uuid4() for slug in SLUGS}


def _name(slug: str) -> str:
    return slug.replace("-", " ").title()


def _concept(ids: dict[str, uuid.UUID], slug: str) -> Concept:
    return Concept(
        id=ids[slug],
        name=_name(slug),
        slug=slug,
        difficulty=DIFFICULTY[slug],
        confidence=0.9,
        verified=False,
    )


def _closure_node(ids: dict[str, uuid.UUID], slug: str, *, depth: int = 1) -> ClosureNode:
    return ClosureNode(
        concept_id=ids[slug],
        name=_name(slug),
        slug=slug,
        difficulty=DIFFICULTY[slug],
        depth=depth,
        path=(ids[slug],),
        weight=1.0,
        confidence=0.9,
        verified=False,
        provenance_document_id=None,
        provenance_chunk_id=None,
        provenance_page=None,
    )


def _closure(ids: dict[str, uuid.UUID]) -> tuple[ClosureNode, ...]:
    return tuple(_closure_node(ids, slug) for slug in SLUGS if slug != "transformers")


def _edges(ids: dict[str, uuid.UUID]) -> tuple[ClosureEdge, ...]:
    edges = [
        ClosureEdge(
            source_concept_id=ids[source],
            target_concept_id=ids[target],
            verified=False,
            weight=1.0,
        )
        for source, target in REQUIRES
    ]
    return tuple(edges)


def _gaps(ids: dict[str, uuid.UUID], *, skip: tuple[str, ...] = ()) -> tuple[KnowledgeGap, ...]:
    return tuple(
        KnowledgeGap(
            concept_id=ids[slug],
            name=_name(slug),
            slug=slug,
            difficulty=DIFFICULTY[slug],
            depth=1,
            path=(ids[slug],),
            mastery=0.0,
            never_assessed=True,
        )
        for slug in SLUGS
        if slug != "transformers" and slug not in skip
    )


def _plan(
    ids: dict[str, uuid.UUID],
    *,
    mastery: dict[uuid.UUID, float] | None = None,
    gaps: tuple[KnowledgeGap, ...] | None = None,
    closure: tuple[ClosureNode, ...] | None = None,
    edges: tuple[ClosureEdge, ...] | None = None,
):
    return build_plan(
        goal=_concept(ids, "transformers"),
        closure=closure if closure is not None else _closure(ids),
        gaps=gaps if gaps is not None else _gaps(ids),
        edges=edges if edges is not None else _edges(ids),
        mastery=mastery or {},
        user_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        goal_text="Transformers",
        reason="Initial plan",
    )


class TestTopologicalOrdering:
    """Kahn with a deterministic tie-break, and a cycle reported not hidden."""

    def test_order_is_topological_and_deterministic(self) -> None:
        ids = _ids()
        closure = _closure(ids)
        edges = _edges(ids)
        goal = _concept(ids, "transformers")
        nodes = [*closure, _as_goal_node(goal)]
        dependencies: dict[uuid.UUID, set[uuid.UUID]] = {node.concept_id: set() for node in nodes}
        for edge in edges:
            dependencies[edge.source_concept_id].add(edge.target_concept_id)
        priority = {
            node.concept_id: (node.difficulty, 0 if node.verified else 1, -node.weight, node.slug)
            for node in nodes
        }
        first, unresolved_first = topological_order(
            [node.concept_id for node in nodes], dependencies, priority
        )
        second, unresolved_second = topological_order(
            [node.concept_id for node in nodes], dependencies, priority
        )
        assert first == second
        assert unresolved_first == unresolved_second == ()
        ordered_slugs = [next(n.slug for n in nodes if n.concept_id == cid) for cid in first]
        # Equality of difficulty is broken by slug, so the two difficulty-2
        # prerequisites come first in alphabetical order.
        assert ordered_slugs == [
            "linear-algebra",
            "probability",
            "classical-ml",
            "optimization",
            "deep-learning",
            "transformers",
        ]

    def test_tie_break_prefers_alphabetical_slug(self) -> None:
        alpha, beta = uuid.uuid4(), uuid.uuid4()
        ordered, unresolved = topological_order(
            [beta, alpha],
            {alpha: set(), beta: set()},
            {alpha: (2, 1, -1.0, "alpha"), beta: (2, 1, -1.0, "beta")},
        )
        assert ordered == (alpha, beta)
        assert unresolved == ()

    def test_cycle_is_marked_and_terminates(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ordered, unresolved = topological_order(
            [a, b, c],
            {a: {b}, b: {a}, c: set()},
            {a: (3, 1, -1.0, "a"), b: (3, 1, -1.0, "b"), c: (1, 1, -1.0, "c")},
        )
        # The acyclic node is still ordered; the cycle is reported, never looped.
        assert ordered == (c,)
        assert unresolved == tuple(sorted((a, b)))

    def test_strict_ordering_raises_a_typed_error(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        with pytest.raises(RoadmapCycleError):
            topological_order(
                [a, b],
                {a: {b}, b: {a}},
                {a: (3, 1, -1.0, "a"), b: (3, 1, -1.0, "b")},
                strict=True,
            )


class TestEffortFormula:
    """The estimate is a documented function of difficulty and prerequisite fan-in."""

    def test_hand_computed_values(self) -> None:
        # 2.0 base + 0.5 * 2 prerequisites = 3.0 hours.
        assert estimate_step_hours(3, 2) == pytest.approx(3.0)
        assert estimate_step_hours(5, 0) == pytest.approx(5.0)
        assert estimate_step_hours(1, 0) == pytest.approx(0.5)

    def test_estimate_reports_its_inputs(self) -> None:
        estimate = estimate_step(4, 3)
        assert estimate.base_hours == BASE_HOURS_BY_DIFFICULTY[4]
        assert estimate.prerequisite_count == 3
        assert estimate.prerequisite_hours == pytest.approx(1.5)
        assert estimate.hours == pytest.approx(5.0)
        assert estimate.clipped is False

    def test_estimate_is_clipped_and_says_so(self) -> None:
        estimate = estimate_step(5, 200)
        assert estimate.hours == MAX_STEP_HOURS
        assert estimate.clipped is True

    def test_estimate_is_deterministic(self) -> None:
        assert estimate_step_hours(3, 2) == estimate_step_hours(3, 2)


def _as_goal_node(goal: Concept) -> ClosureNode:
    """The goal as a closure node, mirroring what the planner builds internally."""
    return ClosureNode(
        concept_id=goal.id,
        name=goal.name,
        slug=goal.slug,
        difficulty=goal.difficulty,
        depth=0,
        path=(goal.id,),
        weight=1.0,
        confidence=float(goal.confidence),
        verified=goal.verified,
        provenance_document_id=None,
        provenance_chunk_id=None,
        provenance_page=None,
    )


class TestBuildPlan:
    def test_subtracts_mastered_concepts(self) -> None:
        ids = _ids()
        plan = _plan(
            ids,
            mastery={ids["linear-algebra"]: 0.9},
            gaps=_gaps(ids, skip=("linear-algebra",)),
        )
        slugs = [step.title for step in plan.steps]
        assert "Linear Algebra" not in slugs
        assert [step.order_index for step in plan.steps] == list(range(len(plan.steps)))

    def test_blocked_by_lists_unmastered_prerequisites_only(self) -> None:
        ids = _ids()
        plan = _plan(
            ids,
            mastery={ids["linear-algebra"]: 0.9},
            gaps=_gaps(ids, skip=("linear-algebra",)),
        )
        by_concept = {step.concept_id: step for step in plan.steps}
        classical = by_concept[ids["classical-ml"]]
        # Probability is unmastered and still blocks; linear algebra is mastered
        # and therefore no longer a blocker.
        assert classical.blocked_by == (ids["probability"],)
        deep = by_concept[ids["deep-learning"]]
        assert set(deep.blocked_by) == {ids["classical-ml"], ids["optimization"]}

    def test_first_step_available_and_blocked_steps_marked(self) -> None:
        ids = _ids()
        plan = _plan(ids)
        assert plan.steps[0].status is RoadmapStepStatus.AVAILABLE
        blocked = [step for step in plan.steps if step.blocked_by]
        assert blocked, "the fixture DAG must produce at least one blocked step"
        assert all(step.status is RoadmapStepStatus.BLOCKED for step in blocked)

    def test_order_matches_topological_order(self) -> None:
        ids = _ids()
        plan = _plan(ids)
        slugs = [step.title for step in plan.steps]
        assert slugs.index("Linear Algebra") < slugs.index("Classical Ml")
        assert slugs.index("Classical Ml") < slugs.index("Deep Learning")
        assert slugs.index("Deep Learning") < slugs.index("Transformers")

    def test_empty_graph_is_degraded_but_not_empty(self) -> None:
        ids = _ids()
        plan = _plan(ids, gaps=(), closure=(), edges=())
        assert len(plan.steps) == 1
        assert plan.steps[0].concept_id == ids["transformers"]
        assert plan.steps[0].status is RoadmapStepStatus.AVAILABLE
        assert "knowledge_graph_empty" in plan.degraded

    def test_unresolved_cycle_is_marked_and_a_partial_order_emitted(self) -> None:
        ids = _ids()
        a, b, c = ids["classical-ml"], ids["deep-learning"], ids["probability"]
        goal = _concept(ids, "transformers")
        closure = (
            _closure_node(ids, "classical-ml"),
            _closure_node(ids, "deep-learning"),
            _closure_node(ids, "probability"),
        )
        edges = (
            ClosureEdge(source_concept_id=goal.id, target_concept_id=a, verified=False, weight=1.0),
            ClosureEdge(source_concept_id=a, target_concept_id=b, verified=False, weight=1.0),
            ClosureEdge(source_concept_id=b, target_concept_id=a, verified=False, weight=1.0),
            ClosureEdge(source_concept_id=goal.id, target_concept_id=c, verified=False, weight=1.0),
        )
        gaps = tuple(
            KnowledgeGap(
                concept_id=concept_id,
                name=_name(slug),
                slug=slug,
                difficulty=DIFFICULTY[slug],
                depth=1,
                path=(concept_id,),
                mastery=0.0,
                never_assessed=True,
            )
            for slug, concept_id in (
                ("classical-ml", a),
                ("deep-learning", b),
                ("probability", c),
            )
        )
        plan = build_plan(
            goal=goal,
            closure=closure,
            gaps=gaps,
            edges=edges,
            mastery={},
            user_id=uuid.uuid4(),
            course_id=None,
            goal_text="Transformers",
            reason="Initial plan",
        )
        assert plan.unresolved_cycle
        assert "unresolved_cycle" in plan.degraded
        # The acyclic prerequisite is still scheduled; the cycle is not hidden.
        assert [step.concept_id for step in plan.steps] == [c]

    def test_total_hours_is_the_sum_of_step_estimates(self) -> None:
        ids = _ids()
        plan = _plan(ids)
        assert plan.estimated_hours == pytest.approx(
            round(sum(step.estimated_hours for step in plan.steps), 4)
        )


class TestDegradedPlan:
    def test_single_step_from_course_metadata(self) -> None:
        plan = degraded_plan(
            user_id=uuid.uuid4(),
            course_id=uuid.uuid4(),
            goal_text="Quantum field theory",
            reason="Initial plan",
            course_name="Physics 101",
            course_description="An introduction to classical and modern physics.",
        )
        assert len(plan.steps) == 1
        step = plan.steps[0]
        assert step.concept_id is None
        assert step.title == "Physics 101"
        assert step.description.startswith("An introduction")
        assert step.estimated_hours == DEGRADED_STEP_HOURS
        assert plan.degraded == ("goal_unresolved",)

    def test_goal_text_is_the_fallback_title(self) -> None:
        plan = degraded_plan(
            user_id=uuid.uuid4(),
            course_id=None,
            goal_text="Understand attention",
            reason="Initial plan",
        )
        assert plan.steps[0].title == "Understand attention"

    def test_goal_unresolved_error_is_typed(self) -> None:
        error = GoalUnresolvedError("nope")
        assert error.status_code == 404
        assert error.error_code == "goal_unresolved"
