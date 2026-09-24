"""The executor: validation, retries, timeouts, permissions and audit.

The executor is the single place a capability is granted, so these tests are the
enforcement tests for the whole tool layer. Each one injects a failure and
asserts both the typed record and the observable side effect (or its absence).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from coursellm.agents.state import ConversationState, initial_state
from coursellm.core.config import Settings
from coursellm.tools.registry import (
    Permission,
    RetryableToolError,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)

pytestmark = pytest.mark.unit


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    api_key: str | None = None


class QuizArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: uuid.UUID


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


async def _noop_handler(args: Any, ctx: Any) -> str:
    return "ok"


def _state() -> ConversationState:
    return initial_state(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        deadline_ns=0,
    )


def _registry(
    *,
    handler: Any,
    name: str = "search_documents",
    side_effects: str = "none",
    permissions: frozenset[Permission] = frozenset({Permission.DOCUMENTS_READ}),
    timeout_ms: int = 1000,
    parameters: type[BaseModel] = SearchArgs,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=name,
            description="test tool",
            parameters=parameters,
            required_permissions=permissions,
            side_effects=side_effects,  # type: ignore[arg-type]
            timeout_ms=timeout_ms,
            handler=handler,
        )
    )
    return registry


class TestArgumentValidation:
    async def test_extra_fields_are_rejected(self) -> None:
        counter = _Counter()

        async def handler(args: SearchArgs, ctx: Any) -> str:
            counter.calls += 1
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="tutor",
            tool="search_documents",
            arguments={"query": "x", "unexpected": True},
            state=_state(),
        )
        assert outcome.record["status"] == "error"
        assert (outcome.record["error"] or "").startswith("ToolArgumentError")
        assert counter.calls == 0

    async def test_a_declared_argument_is_accepted(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            return args.query

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="tutor", tool="search_documents", arguments={"query": "x"}, state=_state()
        )
        assert outcome.record["status"] == "ok"
        assert outcome.result == "x"


class TestTimeout:
    async def test_a_slow_tool_records_timeout(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            await asyncio.sleep(0.2)
            return "late"

        settings = Settings(_env_file=None, agent_tool_max_retries=0)
        executor = ToolExecutor(_registry(handler=handler, timeout_ms=10), settings=settings)
        outcome = await executor.call(
            agent="tutor", tool="search_documents", arguments={"query": "x"}, state=_state()
        )
        assert outcome.record["status"] == "timeout"
        assert outcome.result is None


class TestRetries:
    async def test_read_only_tool_retries_once_on_a_retryable_error(self) -> None:
        counter = _Counter()

        async def handler(args: SearchArgs, ctx: Any) -> str:
            counter.calls += 1
            if counter.calls == 1:
                raise RetryableToolError("transient")
            return "recovered"

        settings = Settings(_env_file=None, agent_tool_max_retries=1)
        executor = ToolExecutor(_registry(handler=handler), settings=settings)
        outcome = await executor.call(
            agent="tutor", tool="search_documents", arguments={"query": "x"}, state=_state()
        )
        assert outcome.record["status"] == "ok"
        assert outcome.result == "recovered"
        assert counter.calls == 2

    async def test_write_tool_does_not_retry(self) -> None:
        counter = _Counter()

        async def handler(args: QuizArgs, ctx: Any) -> str:
            counter.calls += 1
            raise RetryableToolError("transient")

        settings = Settings(_env_file=None, agent_tool_max_retries=3)
        executor = ToolExecutor(
            _registry(
                handler=handler,
                name="create_quiz",
                side_effects="write",
                permissions=frozenset({Permission.QUIZ_WRITE, Permission.DOCUMENTS_READ}),
                parameters=QuizArgs,
            ),
            settings=settings,
        )
        outcome = await executor.call(
            agent="assessment",
            tool="create_quiz",
            arguments={"course_id": str(uuid.uuid4())},
            state=_state(),
        )
        assert outcome.record["status"] == "error"
        assert counter.calls == 1
        assert len(executor.records) == 1


class TestAudit:
    async def test_every_failed_attempt_is_recorded(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            raise RetryableToolError("still transient")

        settings = Settings(_env_file=None, agent_tool_max_retries=2)
        executor = ToolExecutor(_registry(handler=handler), settings=settings)
        await executor.call(
            agent="tutor", tool="search_documents", arguments={"query": "x"}, state=_state()
        )
        assert len(executor.records) == 3
        assert all(record["status"] == "error" for record in executor.records)

    async def test_unknown_tool_is_recorded_and_never_raises(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="tutor", tool="not_a_tool", arguments={}, state=_state()
        )
        assert outcome.record["status"] == "unknown_tool"
        assert len(executor.records) == 1

    def test_require_raises_the_typed_unknown_tool_error(self) -> None:
        from coursellm.tools.registry import UnknownToolError

        registry = _registry(handler=_noop_handler)
        with pytest.raises(UnknownToolError):
            registry.require("send_email")

    async def test_permission_denial_is_recorded_before_the_handler(self) -> None:
        counter = _Counter()

        async def handler(args: SearchArgs, ctx: Any) -> str:
            counter.calls += 1
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="progress", tool="search_documents", arguments={"query": "x"}, state=_state()
        )
        assert outcome.record["status"] == "denied"
        assert counter.calls == 0
        assert len(executor.records) == 1

    async def test_sensitive_arguments_are_redacted_in_the_record(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="tutor",
            tool="search_documents",
            arguments={"query": "x", "api_key": "sk-super-secret-value"},
            state=_state(),
        )
        assert outcome.record["status"] == "ok"
        assert outcome.record["arguments"]["api_key"] == "[redacted]"
        assert outcome.record["arguments"]["query"] == "x"


class TestExternalPolicy:
    async def test_external_tool_is_denied_when_the_flag_is_off(self) -> None:
        counter = _Counter()

        async def handler(args: SearchArgs, ctx: Any) -> str:
            counter.calls += 1
            return "ok"

        settings = Settings(_env_file=None, agent_allow_external_tools=False)
        executor = ToolExecutor(
            _registry(
                handler=handler,
                name="search_web_sources",
                side_effects="external",
                permissions=frozenset({Permission.WEB_READ}),
            ),
            settings=settings,
        )
        outcome = await executor.call(
            agent="recommender",
            tool="search_web_sources",
            arguments={"query": "x"},
            state=_state(),
        )
        assert outcome.record["status"] == "denied"
        assert counter.calls == 0

    async def test_external_tool_runs_when_the_flag_is_on(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            return "ok"

        settings = Settings(_env_file=None, agent_allow_external_tools=True)
        executor = ToolExecutor(
            _registry(
                handler=handler,
                name="search_web_sources",
                side_effects="external",
                permissions=frozenset({Permission.WEB_READ}),
            ),
            settings=settings,
        )
        outcome = await executor.call(
            agent="recommender",
            tool="search_web_sources",
            arguments={"query": "x"},
            state=_state(),
        )
        assert outcome.record["status"] == "ok"


class TestScopeInjection:
    async def test_handler_receives_tenant_from_state_not_arguments(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(args: SearchArgs, ctx: Any) -> str:
            seen["tenant_id"] = ctx.tenant_id
            seen["user_id"] = ctx.user_id
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        state = _state()
        await executor.call(
            agent="tutor", tool="search_documents", arguments={"query": "x"}, state=state
        )
        assert seen["tenant_id"] == state["tenant_id"]
        assert seen["user_id"] == state["user_id"]

    async def test_tenant_id_is_not_an_accepted_argument(self) -> None:
        async def handler(args: SearchArgs, ctx: Any) -> str:
            return "ok"

        executor = ToolExecutor(_registry(handler=handler), settings=Settings(_env_file=None))
        outcome = await executor.call(
            agent="tutor",
            tool="search_documents",
            arguments={"query": "x", "tenant_id": str(uuid.uuid4())},
            state=_state(),
        )
        assert outcome.record["status"] == "error"
        assert (outcome.record["error"] or "").startswith("ToolArgumentError")
