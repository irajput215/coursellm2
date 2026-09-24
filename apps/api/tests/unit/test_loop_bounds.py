"""Every bound is exercised with a fake model that never stops asking for more.

These tests are the executable form of ``agent-architecture.md`` section 9. The
contract they check is not "the loop stops" — it is "*how* it stops": at
``answer_composer``, with a ``degraded`` reason and a non-empty answer, whatever
the counter or the clock did.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from coursellm.agents.graph import build_graph
from coursellm.agents.nodes.retrieval import RetrievalResult
from coursellm.agents.state import ConversationState, initial_state
from coursellm.core.config import Settings
from coursellm.llm.types import LLMRequest, LLMResponse

pytestmark = pytest.mark.unit

_RETRIEVE = json.dumps(
    {
        "action": "retrieve",
        "tool_calls": [{"name": "search_documents", "arguments": {"query": "attention"}}],
        "answer": None,
        "rationale": "always wants more",
    }
)
_TOOL = json.dumps(
    {
        "action": "tool",
        "tool_calls": [{"name": "get_student_progress", "arguments": {}}],
        "answer": None,
        "rationale": "always wants a tool",
    }
)
_ROUTER = json.dumps({"intent": "tutor", "confidence": 0.9, "reason": "question"})


class ScriptedGateway:
    """Returns queued texts forever, repeating the last one when exhausted."""

    def __init__(self, *items: Any) -> None:
        self._items = list(items)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._items.pop(0) if self._items else _RETRIEVE
        parsed = None
        if request.response_model is not None:
            parsed = request.response_model.model_validate_json(item)
        return LLMResponse(
            text=item,
            parsed=parsed,
            model="scripted",
            provider="scripted",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.0,
            latency_ms=1.0,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        yield ""


async def _empty_retrieve(state: ConversationState, plan: Any) -> RetrievalResult:
    return RetrievalResult(documents=[], degraded=[])


def _state(*, deadline_ns: int | None = None) -> ConversationState:
    return initial_state(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        deadline_ns=deadline_ns or time.monotonic_ns() + 60_000_000_000,
        messages=[HumanMessage(content="Explain self attention")],
    )


async def _run(
    settings: Settings,
    gateway: ScriptedGateway,
    *,
    deadline_ns: int | None = None,
) -> dict[str, Any]:
    graph = build_graph(settings=settings, gateway=gateway, retrieve=_empty_retrieve)
    result = await graph.ainvoke(
        _state(deadline_ns=deadline_ns),
        {"recursion_limit": settings.graph_recursion_limit},
    )
    return dict(result)


def _reasons(state: dict[str, Any]) -> set[str]:
    return {str(reason) for reason in state.get("degraded") or []}


class TestIterationLimit:
    async def test_an_always_retrieving_agent_terminates_at_the_step_ceiling(
        self, settings: Settings
    ) -> None:
        bounded = settings.model_copy(
            update={"graph_max_steps": 3, "agent_max_retrieval_passes": 10}
        )
        gateway = ScriptedGateway(_ROUTER, _RETRIEVE, _RETRIEVE, _RETRIEVE)
        result = await _run(bounded, gateway)

        assert result["iteration_count"] == bounded.graph_max_steps
        assert "iteration_limit_reached" in _reasons(result)
        assert result["answer_draft"]["text"]

    async def test_the_bounded_turn_makes_no_further_model_call(self, settings: Settings) -> None:
        bounded = settings.model_copy(
            update={"graph_max_steps": 3, "agent_max_retrieval_passes": 10}
        )
        gateway = ScriptedGateway(_ROUTER, _RETRIEVE, _RETRIEVE, _RETRIEVE)
        await _run(bounded, gateway)
        # Router + one per agent execution. The composer must not add a call.
        assert len(gateway.requests) == 4


class TestToolLimit:
    async def test_the_tool_ceiling_stops_the_turn(self, settings: Settings) -> None:
        bounded = settings.model_copy(
            update={
                "agent_max_tool_calls_per_turn": 1,
                "graph_max_steps": 8,
                "agent_max_retrieval_passes": 10,
            }
        )
        gateway = ScriptedGateway(_ROUTER, _RETRIEVE, _RETRIEVE)
        result = await _run(bounded, gateway)

        assert result["tool_call_count"] == 1
        assert "tool_limit_reached" in _reasons(result)
        assert result["answer_draft"]["text"]

    async def test_a_non_retrieval_tool_loop_terminates(self, settings: Settings) -> None:
        gateway = ScriptedGateway(_ROUTER, _TOOL)
        result = await _run(settings, gateway)
        assert result["tool_calls"]
        assert result["answer_draft"]["text"]


class TestTokenBudget:
    async def test_a_token_ceiling_produces_an_extractive_answer(self, settings: Settings) -> None:
        bounded = settings.model_copy(update={"agent_max_tokens_per_turn": 1})
        gateway = ScriptedGateway(_ROUTER)
        result = await _run(bounded, gateway)

        assert "token_budget_exceeded" in _reasons(result)
        assert result["answer_draft"]["text"]
        # Only the router ran; the agent and composer both refused to spend more.
        assert len(gateway.requests) == 1


class TestDeadline:
    async def test_a_past_deadline_makes_no_model_call_at_all(self, settings: Settings) -> None:
        gateway = ScriptedGateway()
        past = time.monotonic_ns() - 1
        result = await _run(settings, gateway, deadline_ns=past)

        assert gateway.requests == []
        assert "deadline_exceeded" in _reasons(result)
        assert result["answer_draft"]["text"]


class TestRecursionLimit:
    async def test_the_recursion_limit_is_derived_from_the_step_ceiling(self) -> None:
        derived = Settings(_env_file=None, graph_max_steps=5, graph_recursion_limit=0)
        assert derived.graph_recursion_limit == 2 * 5 + 8

    def test_an_explicit_recursion_limit_wins(self) -> None:
        explicit = Settings(_env_file=None, graph_max_steps=5, graph_recursion_limit=99)
        assert explicit.graph_recursion_limit == 99
