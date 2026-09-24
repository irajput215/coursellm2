"""The permission matrix is the test data.

Every ``(agent, tool)`` row in ``coursellm.tools.permissions.PERMISSION_MATRIX``
becomes a test case, so enforcement and specification cannot drift. The three
capabilities that are denied to every agent are tested for *absence* from the
registry, which is the excessive-agency control: a model that emits the name gets
``unknown_tool`` and there is no schema to be persuaded by.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from coursellm.agents.state import ConversationState, initial_state
from coursellm.core.config import Settings
from coursellm.tools import build_tool_registry
from coursellm.tools.permissions import (
    AGENT_NAMES,
    AGENT_TOOLS,
    DENIED_CAPABILITIES,
    FORBIDDEN_PERMISSIONS,
    PERMISSION_MATRIX,
    REGISTERED_TOOL_NAMES,
)
from coursellm.tools.registry import (
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)

pytestmark = pytest.mark.unit

_TENANT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class FakeSession:
    """Records every statement. A denied call must leave this at zero."""

    def __init__(self) -> None:
        self.queries = 0

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.queries += 1
        raise AssertionError("A tool handler reached the database on a denied call.")


def _state() -> ConversationState:
    return initial_state(
        tenant_id=_TENANT,
        user_id=_USER,
        conversation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        roles=frozenset({"owner"}),
        deadline_ns=0,
    )


def _web_settings() -> Settings:
    return Settings(
        _env_file=None,
        agent_web_search_enabled=True,
        agent_allow_external_tools=True,
    )


def _default_settings() -> Settings:
    return Settings(_env_file=None)


def _recording_registry(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """A registry whose handlers record invocation but keep the real permissions."""
    real = build_tool_registry(settings)
    registry = ToolRegistry()
    calls: list[str] = []

    for spec in real.specs():

        async def handler(args: Any, ctx: Any, _name: str = spec.name) -> dict[str, Any]:
            calls.append(_name)
            return {"tool": _name}

        registry.register(
            ToolSpec(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                required_permissions=spec.required_permissions,
                side_effects=spec.side_effects,
                timeout_ms=spec.timeout_ms,
                handler=handler,
            )
        )
    return registry, calls


def _fake_arguments(tool: str) -> dict[str, Any]:
    return {
        "search_documents": {"query": "attention"},
        "search_books": {"query": "attention"},
        "search_course": {"course_id": str(uuid.uuid4())},
        "search_knowledge_graph": {"concept_name": "attention"},
        "search_web_sources": {"query": "attention"},
        "get_student_progress": {},
        "create_quiz": {"course_id": str(uuid.uuid4())},
        "evaluate_answer": {
            "quiz_attempt_id": str(uuid.uuid4()),
            "item_id": "item-1",
            "answer": "self attention",
        },
        "update_learning_plan": {
            "course_id": str(uuid.uuid4()),
            "goal_concept_id": "transformers",
            "steps": [],
            "idempotency_key": "key-1",
        },
        "get_recommendations": {"course_id": str(uuid.uuid4())},
        # Declared but never registered; the registry rejects them before
        # argument validation, so an empty payload is correct here.
        "send_email": {},
        "delete_document": {},
        "run_sql": {},
    }[tool]


class TestRegistryShape:
    def test_every_matrix_tool_is_registered_when_its_flag_is_on(self) -> None:
        registry = build_tool_registry(_web_settings())
        assert set(REGISTERED_TOOL_NAMES) <= registry.names()

    def test_the_three_denied_capabilities_are_never_registered(self) -> None:
        for settings in (_default_settings(), _web_settings()):
            registry = build_tool_registry(settings)
            for capability in DENIED_CAPABILITIES:
                assert capability not in registry
                assert registry.get(capability) is None

    def test_no_registered_tool_accepts_tenant_or_user_arguments(self) -> None:
        registry = build_tool_registry(_web_settings())
        for spec in registry.specs():
            properties = spec.parameters.model_json_schema().get("properties", {})
            assert "tenant_id" not in properties, spec.name
            assert "user_id" not in properties, spec.name

    def test_every_registered_tool_belongs_to_the_declared_tool_set(self) -> None:
        """A new tool cannot appear without a matrix row and a permission decision."""
        registry = build_tool_registry(_web_settings())
        assert registry.names() <= set(REGISTERED_TOOL_NAMES)

    def test_web_search_is_absent_by_default(self) -> None:
        assert "search_web_sources" not in build_tool_registry(_default_settings())


@pytest.mark.parametrize(("agent", "tool", "allowed"), PERMISSION_MATRIX)
async def test_permission_matrix(agent: str, tool: str, allowed: bool) -> None:
    settings = _web_settings()
    registry, calls = _recording_registry(settings)
    executor = ToolExecutor(registry, settings=settings, session=FakeSession())  # type: ignore[arg-type]
    before = len(calls)

    outcome = await executor.call(
        agent=agent, tool=tool, arguments=_fake_arguments(tool), state=_state()
    )

    if allowed:
        assert outcome.record["status"] == "ok", (agent, tool, outcome.record)
        assert len(calls) == before + 1
    else:
        expected = "unknown_tool" if tool in DENIED_CAPABILITIES else "denied"
        assert outcome.record["status"] == expected, (agent, tool, outcome.record)
        assert len(calls) == before, f"{agent} reached the {tool} handler on a denied call"


class TestDeniedCallHasNoSideEffect:
    async def test_denied_call_does_not_touch_the_database(self) -> None:
        session = FakeSession()
        settings = _web_settings()
        registry, calls = _recording_registry(settings)
        executor = ToolExecutor(registry, settings=settings, session=session)  # type: ignore[arg-type]

        outcome = await executor.call(
            agent="progress",
            tool="search_documents",
            arguments={"query": "attention"},
            state=_state(),
        )

        assert outcome.record["status"] == "denied"
        assert calls == []
        assert session.queries == 0

    async def test_unknown_capability_never_reaches_a_handler(self) -> None:
        session = FakeSession()
        settings = _web_settings()
        registry, calls = _recording_registry(settings)
        executor = ToolExecutor(registry, settings=settings, session=session)  # type: ignore[arg-type]

        outcome = await executor.call(
            agent="tutor", tool="send_email", arguments={}, state=_state()
        )

        assert outcome.record["status"] == "unknown_tool"
        assert calls == []
        assert session.queries == 0


class TestNoAgentHoldsAForbiddenPermission:
    @pytest.mark.parametrize("agent", AGENT_NAMES)
    def test_forbidden_permissions_are_not_granted(self, agent: str) -> None:
        settings = _web_settings()
        registry, _ = _recording_registry(settings)
        executor = ToolExecutor(registry, settings=settings)
        assert executor.granted(agent) & FORBIDDEN_PERMISSIONS == frozenset()

    @pytest.mark.parametrize("agent", AGENT_NAMES)
    def test_agent_tool_allowlist_is_derived_from_the_matrix(self, agent: str) -> None:
        expected = {
            tool for row_agent, tool, allowed in PERMISSION_MATRIX if row_agent == agent and allowed
        }
        assert set(AGENT_TOOLS[agent]) == expected

    @pytest.mark.parametrize("agent", AGENT_NAMES)
    def test_every_granted_permission_comes_from_an_allowlisted_tool(self, agent: str) -> None:
        settings = _web_settings()
        registry = build_tool_registry(settings)
        executor = ToolExecutor(registry, settings=settings)
        expected: set[Any] = set()
        for name in AGENT_TOOLS[agent]:
            spec = registry.get(name)
            if spec is not None:
                expected.update(spec.required_permissions)
        assert executor.granted(agent) == frozenset(expected)
