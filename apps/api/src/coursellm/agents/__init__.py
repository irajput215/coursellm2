"""The LangGraph agent layer.

The package is the executable form of ``docs/architecture/agent-architecture.md``:
a typed state (``state.py``), pure conditional edges (``routing.py``), budget
guards (``budgets.py``), one module per node (``nodes/``) and the topology that
wires them (``graph.py``).

Public names are resolved lazily. Importing :mod:`coursellm.agents.graph` pulls in
every node and, through them, the tool registry and the retrieval pipeline; a
caller that only needs :class:`ConversationState` or
:func:`~coursellm.agents.routing.route_intent` should not pay for that, and the
lazy ``__getattr__`` also avoids a package-initialisation cycle between
``routing`` and ``graph``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "AgentDecision": "state",
    "AgentError": "state",
    "Citation": "state",
    "ConversationState": "state",
    "DegradationReason": "state",
    "EvaluationMetadata": "state",
    "GraphEntity": "state",
    "Intent": "state",
    "RetrievalPlan": "state",
    "RetrievedDocument": "state",
    "StudentProgress": "state",
    "TokenUsage": "state",
    "ToolCallRecord": "state",
    "initial_state": "state",
    "merge_unique": "state",
    "route_after_agent": "routing",
    "route_after_evidence": "routing",
    "route_intent": "routing",
    "build_graph": "graph",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public name from its submodule on first access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module = import_module(f"coursellm.agents.{module_name}")
    return getattr(module, name)
