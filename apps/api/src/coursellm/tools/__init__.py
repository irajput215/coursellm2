"""Typed, permission-checked tools the agents may call.

The registry is built per settings, not imported as a mutable global, because one
tool's membership is configuration-dependent: ``search_web_sources`` exists only
when ``AGENT_WEB_SEARCH_ENABLED`` is true. Building it explicitly also makes the
capability set a property of the deployed configuration, auditable in one place,
rather than of whatever imported which module first.
"""

from __future__ import annotations

from coursellm.core.config import Settings
from coursellm.tools import documents, graph_tools, planning, progress, quiz, recommend, web
from coursellm.tools.graph_tools import (
    KnowledgeGraphRepository,
    KnowledgeGraphResult,
    NullKnowledgeGraphRepository,
)
from coursellm.tools.permissions import (
    AGENT_NAMES,
    AGENT_TOOLS,
    DENIED_CAPABILITIES,
    FORBIDDEN_PERMISSIONS,
    PERMISSION_MATRIX,
    REGISTERED_TOOL_NAMES,
)
from coursellm.tools.registry import (
    Permission,
    RetryableToolError,
    SideEffects,
    ToolArgumentError,
    ToolContext,
    ToolError,
    ToolExecutor,
    ToolOutcome,
    ToolPermissionError,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
)


def build_tool_registry(
    settings: Settings,
    *,
    graph_repository: KnowledgeGraphRepository | None = None,
) -> ToolRegistry:
    """Build the registry for this deployment.

    The three capabilities that are denied to every agent (``send_email``,
    ``delete_document``, ``run_sql``) are never registered here, at any
    configuration: there is no flag that enables them.
    """
    registry = ToolRegistry()
    documents.register(registry)
    graph_tools.register(registry, repository=graph_repository)
    progress.register(registry)
    quiz.register(registry)
    planning.register(registry)
    recommend.register(registry)
    if settings.agent_web_search_enabled:
        web.register(registry)
    return registry


__all__ = [
    "AGENT_NAMES",
    "AGENT_TOOLS",
    "DENIED_CAPABILITIES",
    "FORBIDDEN_PERMISSIONS",
    "PERMISSION_MATRIX",
    "REGISTERED_TOOL_NAMES",
    "KnowledgeGraphRepository",
    "KnowledgeGraphResult",
    "NullKnowledgeGraphRepository",
    "Permission",
    "RetryableToolError",
    "SideEffects",
    "ToolArgumentError",
    "ToolContext",
    "ToolError",
    "ToolExecutor",
    "ToolOutcome",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolSpec",
    "UnknownToolError",
    "build_tool_registry",
]
