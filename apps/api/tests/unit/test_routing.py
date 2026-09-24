"""The three conditional edges are pure, so they are tested with no fake at all.

Each test is a state and an expected routing decision. If any of these functions
ever needs to await something, the test file stops compiling — which is the point:
the graph's control flow must not depend on I/O.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

from coursellm.agents.routing import (
    assess_grounding,
    route_after_agent,
    route_after_evidence,
    route_intent,
)
from coursellm.agents.state import ConversationState, Intent, RetrievedDocument, initial_state
from coursellm.core.config import Settings

pytestmark = pytest.mark.unit

_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER = uuid.UUID("22222222-2222-2222-2222-222222222222")
_CONVERSATION = uuid.UUID("33333333-3333-3333-3333-333333333333")
_REQUEST = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _state(**overrides: Any) -> ConversationState:
    state = initial_state(
        tenant_id=_TENANT,
        user_id=_USER,
        conversation_id=_CONVERSATION,
        request_id=_REQUEST,
        roles=frozenset({"owner"}),
        deadline_ns=time.monotonic_ns() + 60_000_000_000,
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _document(*, citation_id: str = "S1", rerank_score: float | None = None) -> RetrievedDocument:
    return RetrievedDocument(
        chunk_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        content="A passage.",
        page=1,
        topic=None,
        source_type="other",
        semantic_rank=1,
        lexical_rank=1,
        graph_rank=None,
        rrf_score=0.5,
        rerank_score=rerank_score,
        citation_id=citation_id,
    )


class TestRouteIntent:
    @pytest.mark.parametrize(
        "intent", ["tutor", "planner", "recommender", "assessment", "progress"]
    )
    def test_each_routable_intent_routes_to_its_agent(self, intent: Intent) -> None:
        assert route_intent(_state(intent=intent)) == intent

    def test_absent_intent_falls_back_to_tutor(self) -> None:
        assert route_intent(_state()) == "tutor"

    def test_unknown_intent_falls_back_to_tutor(self) -> None:
        state = _state()
        state["intent"] = "astrology"  # type: ignore[typeddict-item]
        assert route_intent(state) == "tutor"

    def test_non_string_intent_falls_back_to_tutor(self) -> None:
        state = _state()
        state["intent"] = None  # type: ignore[typeddict-item]
        assert route_intent(state) == "tutor"


class TestRouteAfterAgent:
    def test_no_pending_calls_composes(self, settings: Settings) -> None:
        assert route_after_agent(_state(), settings) == "compose"

    def test_retrieval_call_routes_to_the_retrieval_sub_path(self, settings: Settings) -> None:
        state = _state(pending_tool_calls=[{"name": "search_documents", "arguments": {}}])
        assert route_after_agent(state, settings) == "retrieval"

    def test_non_retrieval_call_routes_to_tools(self, settings: Settings) -> None:
        state = _state(pending_tool_calls=[{"name": "get_student_progress", "arguments": {}}])
        assert route_after_agent(state, settings) == "tools"

    def test_retrieval_call_past_the_pass_limit_falls_through_to_tools(
        self, settings: Settings
    ) -> None:
        state = _state(
            pending_tool_calls=[{"name": "search_documents", "arguments": {}}],
            retrieval_pass=settings.agent_max_retrieval_passes,
        )
        assert route_after_agent(state, settings) == "tools"

    def test_iteration_ceiling_composes(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"graph_max_steps": 2})
        state = _state(
            iteration_count=2,
            pending_tool_calls=[{"name": "search_documents", "arguments": {}}],
        )
        assert route_after_agent(state, bounded) == "compose"

    def test_tool_ceiling_composes(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"agent_max_tool_calls_per_turn": 1})
        state = _state(
            tool_call_count=1,
            pending_tool_calls=[{"name": "search_documents", "arguments": {}}],
        )
        assert route_after_agent(state, bounded) == "compose"

    def test_expired_deadline_composes(self, settings: Settings) -> None:
        state = _state(
            deadline_ns=time.monotonic_ns() - 1,
            pending_tool_calls=[{"name": "search_documents", "arguments": {}}],
        )
        assert route_after_agent(state, settings) == "compose"


class TestRouteAfterEvidence:
    def test_grounded_evidence_composes(self, settings: Settings) -> None:
        state = _state(
            retrieved_documents=[_document(rerank_score=0.9)],
            evaluation_metadata={**_state()["evaluation_metadata"], "grounded": True},
        )
        assert route_after_evidence(state, settings) == "answer_composer"

    def test_pending_calls_prevent_composition(self, settings: Settings) -> None:
        state = _state(
            retrieved_documents=[_document()],
            evaluation_metadata={**_state()["evaluation_metadata"], "grounded": True},
            pending_tool_calls=[{"name": "get_student_progress", "arguments": {}}],
        )
        assert route_after_evidence(state, settings) == "tutor_agent"

    def test_out_of_evidence_passes_composes(self, settings: Settings) -> None:
        state = _state(retrieval_pass=settings.agent_max_retrieval_passes)
        assert route_after_evidence(state, settings) == "answer_composer"

    def test_step_limit_composes(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"graph_max_steps": 1})
        state = _state(iteration_count=1)
        assert route_after_evidence(state, bounded) == "answer_composer"

    @pytest.mark.parametrize(
        "intent", ["tutor", "planner", "recommender", "assessment", "progress"]
    )
    def test_iterates_to_the_routed_agent(self, settings: Settings, intent: Intent) -> None:
        state = _state(intent=intent)
        assert route_after_evidence(state, settings) == f"{intent}_agent"

    def test_unknown_intent_iterates_to_tutor(self, settings: Settings) -> None:
        state = _state()
        state["intent"] = "astrology"  # type: ignore[typeddict-item]
        assert route_after_evidence(state, settings) == "tutor_agent"


class TestAssessGrounding:
    def test_unscored_passages_count_as_evidence(self, settings: Settings) -> None:
        verdict = assess_grounding(_state(retrieved_documents=[_document()]), settings)
        assert verdict["grounded"] is True
        assert verdict["evidence_count"] == 1
        assert verdict["max_rerank_score"] is None

    def test_a_passage_below_the_floor_is_not_evidence(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"grounding_min_rerank_score": 0.0})
        verdict = assess_grounding(
            _state(retrieved_documents=[_document(rerank_score=-1.0)]), bounded
        )
        assert verdict["grounded"] is False
        assert verdict["evidence_count"] == 0
        assert verdict["max_rerank_score"] == -1.0

    def test_evidence_threshold_is_honoured(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"grounding_min_evidence": 2})
        verdict = assess_grounding(_state(retrieved_documents=[_document()]), bounded)
        assert verdict["grounded"] is False

    def test_a_resolvable_citation_counts_as_grounded(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"grounding_min_evidence": 5})
        state = _state(
            citations=[
                {
                    "citation_id": "S1",
                    "chunk_id": str(uuid.uuid4()),
                    "document_id": str(uuid.uuid4()),
                    "page": None,
                    "source_type": "other",
                }
            ]
        )
        assert assess_grounding(state, bounded)["grounded"] is True
