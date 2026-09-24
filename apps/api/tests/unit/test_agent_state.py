"""The state contract: reducers, the initial-state factory, and the write guard."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from coursellm.agents.state import (
    REQUIRED_STATE_KEYS,
    ConversationState,
    DegradationReason,
    RetrievedDocument,
    UndeclaredStateKeyError,
    add_usage,
    empty_usage,
    initial_state,
    merge_unique,
    message_history,
    validate_update,
)

pytestmark = pytest.mark.unit


def _document(content: str = "A passage.") -> RetrievedDocument:
    return RetrievedDocument(
        chunk_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        content=content,
        page=None,
        topic=None,
        source_type="other",
        semantic_rank=None,
        lexical_rank=None,
        graph_rank=None,
        rrf_score=1.0,
        rerank_score=None,
        citation_id="S1",
    )


def _state() -> ConversationState:
    return initial_state(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        deadline_ns=time.monotonic_ns() + 60_000_000_000,
    )


class TestMergeUnique:
    def test_appends_new_items_in_first_seen_order(self) -> None:
        assert merge_unique(["a", "b"], ["c", "a", "d"]) == ["a", "b", "c", "d"]

    def test_does_not_mutate_either_side(self) -> None:
        left = ["a"]
        right = ["b"]
        result = merge_unique(left, right)
        assert result == ["a", "b"]
        assert left == ["a"]
        assert right == ["b"]

    def test_empty_sides_are_identity(self) -> None:
        assert merge_unique([], ["x"]) == ["x"]
        assert merge_unique(["x"], []) == ["x"]

    def test_structurally_equal_dicts_are_deduplicated(self) -> None:
        assert merge_unique([{"a": 1}], [{"a": 1}]) == [{"a": 1}]


class TestReducersAppendRatherThanReplace:
    """A two-node graph that writes the same append-only channels twice."""

    async def _run(self) -> dict[str, Any]:
        builder: StateGraph[ConversationState] = StateGraph(ConversationState)

        async def first(state: ConversationState) -> dict[str, Any]:
            return {
                "retrieved_documents": [_document("first")],
                "degraded": [DegradationReason.RETRIEVAL_EMPTY],
            }

        async def second(state: ConversationState) -> dict[str, Any]:
            return {
                "retrieved_documents": [_document("second")],
                "degraded": [DegradationReason.RETRIEVAL_EMPTY],
            }

        builder.add_node("first", first)
        builder.add_node("second", second)
        builder.add_edge(START, "first")
        builder.add_edge("first", "second")
        builder.add_edge("second", END)
        graph = builder.compile()
        result = await graph.ainvoke(_state())
        return dict(result)

    async def test_evidence_accumulates(self) -> None:
        result = await self._run()
        contents = [document["content"] for document in result["retrieved_documents"]]
        assert contents == ["first", "second"]

    async def test_degraded_is_a_set(self) -> None:
        result = await self._run()
        assert result["degraded"] == [DegradationReason.RETRIEVAL_EMPTY]


class TestInitialState:
    def test_populates_every_required_key(self) -> None:
        assert set(_state()) >= REQUIRED_STATE_KEYS

    def test_counters_start_at_zero(self) -> None:
        state = _state()
        assert state["iteration_count"] == 0
        assert state["retrieval_pass"] == 0
        assert state["tool_call_count"] == 0

    def test_deadline_is_required_and_preserved(self) -> None:
        deadline = time.monotonic_ns() + 1_000_000_000
        state = initial_state(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            roles=frozenset({"owner"}),
            deadline_ns=deadline,
        )
        assert state["deadline_ns"] == deadline

    def test_identity_is_written_once_and_carried(self) -> None:
        tenant = uuid.uuid4()
        state = initial_state(
            tenant_id=tenant,
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            roles=frozenset({"owner", "member"}),
            deadline_ns=time.monotonic_ns() + 1_000_000_000,
        )
        assert state["tenant_id"] == tenant
        assert state["roles"] == frozenset({"owner", "member"})


class TestUndeclaredWritesAreCaught:
    def test_validate_update_rejects_an_undeclared_key(self) -> None:
        with pytest.raises(UndeclaredStateKeyError) as excinfo:
            validate_update({"definitely_not_a_channel": 1})
        assert "definitely_not_a_channel" in str(excinfo.value)

    def test_validate_update_passes_a_declared_update(self) -> None:
        assert validate_update({"iteration_count": 1}) == {"iteration_count": 1}

    async def test_langgraph_drops_an_undeclared_write_so_the_guard_must_catch_it(self) -> None:
        """The deliberate violation: it demonstrates why ``validate_update`` exists.

        LangGraph 1.2 silently discards a key the state schema does not declare,
        which is exactly the kind of mutation that would otherwise go unnoticed.
        The node-level guard is therefore the control, and it is asserted here.
        """
        builder: StateGraph[ConversationState] = StateGraph(ConversationState)

        async def rogue(state: ConversationState) -> dict[str, Any]:
            return {"smuggled_key": True}

        builder.add_node("rogue", rogue)
        builder.add_edge(START, "rogue")
        builder.add_edge("rogue", END)
        graph = builder.compile()
        result = await graph.ainvoke(_state())
        assert "smuggled_key" not in result

        with pytest.raises(UndeclaredStateKeyError):
            validate_update({"smuggled_key": True})


class TestTokenUsage:
    def test_add_usage_accumulates_rather_than_replaces(self) -> None:
        first = add_usage(
            empty_usage(), prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.01
        )
        second = add_usage(
            first, prompt_tokens=20, completion_tokens=1, total_tokens=21, cost_usd=0.02
        )
        assert second == {
            "prompt_tokens": 30,
            "completion_tokens": 6,
            "total_tokens": 36,
            "cost_usd": pytest.approx(0.03),
            "calls": 2,
        }

    def test_add_usage_ignores_negative_deltas(self) -> None:
        usage = add_usage(
            None, prompt_tokens=-5, completion_tokens=-1, total_tokens=-6, cost_usd=-1.0
        )
        assert usage["total_tokens"] == 0
        assert usage["calls"] == 1


class TestMessageHistory:
    def test_projects_human_and_ai_turns(self) -> None:
        state = _state()
        state["messages"] = [
            HumanMessage(content="What is attention?"),
            ToolMessage(content="tool output", tool_call_id="1"),
            AIMessage(content="It mixes information [S1]."),
        ]
        history = message_history(state, limit=10)
        assert [(message.role, message.content) for message in history] == [
            ("user", "What is attention?"),
            ("assistant", "It mixes information [S1]."),
        ]

    def test_limit_takes_the_most_recent_turns(self) -> None:
        state = _state()
        state["messages"] = [HumanMessage(content=f"question {index}") for index in range(5)]
        history = message_history(state, limit=2)
        assert [message.content for message in history] == ["question 3", "question 4"]
