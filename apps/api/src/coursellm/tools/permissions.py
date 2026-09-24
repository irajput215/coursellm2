"""The agent permission matrix, as data.

This table is the single source of truth for two things that must never drift:
what :class:`~coursellm.tools.registry.ToolExecutor` enforces, and what the
permission test parametrises over. The test is generated from
:data:`PERMISSION_MATRIX`, so a row added here is a test case added there and a
row removed here removes the test.

Columns are the ten registered tools plus the three capabilities that are
*declared but never registered*:

* ``send_email`` (``email:send``) — an outbound, irreversible side effect.
* ``delete_document`` (``documents:write``) — destructive and cascading.
* ``run_sql`` (``sql:execute``) — arbitrary SQL defeats tenancy and the
  read/write split.

Every agent is denied all three. The denial is enforced twice: this table grants
none of their permissions, and the tools are absent from the registry, so a model
that emits the name gets ``status="unknown_tool"`` rather than a permission
decision it could be persuaded out of.

``search_web_sources`` is marked allowed for the tutor and the recommender in the
matrix because that is their declared capability; registration is additionally
gated on ``AGENT_WEB_SEARCH_ENABLED`` and execution on
``AGENT_ALLOW_EXTERNAL_TOOLS``, so the default deployment has the tool absent and
the external side-effect class refused.
"""

from __future__ import annotations

from coursellm.tools.registry import Permission

#: Agent identifiers used by the matrix, the graph node names and the executor.
AGENT_NAMES: tuple[str, ...] = ("tutor", "planner", "recommender", "assessment", "progress")

#: Ten registered tools, in architecture-document order. The three denied
#: capabilities are deliberately not here.
REGISTERED_TOOL_NAMES: tuple[str, ...] = (
    "search_documents",
    "search_course",
    "search_knowledge_graph",
    "search_books",
    "search_web_sources",
    "get_student_progress",
    "create_quiz",
    "evaluate_answer",
    "update_learning_plan",
    "get_recommendations",
)

#: The three declared-but-unregistered capabilities.
DENIED_CAPABILITIES: dict[str, Permission] = {
    "send_email": Permission.EMAIL_SEND,
    "delete_document": Permission.DOCUMENTS_WRITE,
    "run_sql": Permission.SQL_EXECUTE,
}

#: Capabilities no agent may hold, whether or not a tool requires them.
FORBIDDEN_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENTS_WRITE,
        Permission.GRAPH_WRITE,
        Permission.SQL_EXECUTE,
        Permission.EMAIL_SEND,
    }
)


def _rows() -> tuple[tuple[str, str, bool], ...]:
    """The 5 x 13 matrix as ``(agent, tool, allowed)`` triples."""
    table: dict[str, dict[str, bool]] = {
        "tutor": {
            "search_documents": True,
            "search_course": True,
            "search_knowledge_graph": True,
            "search_books": True,
            "search_web_sources": True,  # only when AGENT_WEB_SEARCH_ENABLED
            "get_student_progress": True,
            "create_quiz": False,
            "evaluate_answer": False,
            "update_learning_plan": False,
            "get_recommendations": False,
            "send_email": False,
            "delete_document": False,
            "run_sql": False,
        },
        "planner": {
            "search_documents": False,
            "search_course": True,
            "search_knowledge_graph": True,
            "search_books": False,
            "search_web_sources": False,
            "get_student_progress": True,
            "create_quiz": False,
            "evaluate_answer": False,
            "update_learning_plan": True,
            "get_recommendations": False,
            "send_email": False,
            "delete_document": False,
            "run_sql": False,
        },
        "recommender": {
            "search_documents": False,
            "search_course": False,
            "search_knowledge_graph": True,
            "search_books": False,
            "search_web_sources": True,  # only when AGENT_WEB_SEARCH_ENABLED
            "get_student_progress": True,
            "create_quiz": False,
            "evaluate_answer": False,
            "update_learning_plan": False,
            "get_recommendations": True,
            "send_email": False,
            "delete_document": False,
            "run_sql": False,
        },
        "assessment": {
            "search_documents": True,
            "search_course": True,
            "search_knowledge_graph": True,
            "search_books": False,
            "search_web_sources": False,
            "get_student_progress": True,
            "create_quiz": True,
            "evaluate_answer": True,
            "update_learning_plan": False,
            "get_recommendations": False,
            "send_email": False,
            "delete_document": False,
            "run_sql": False,
        },
        "progress": {
            "search_documents": False,
            "search_course": True,
            "search_knowledge_graph": True,
            "search_books": False,
            "search_web_sources": False,
            "get_student_progress": True,
            "create_quiz": False,
            "evaluate_answer": False,
            "update_learning_plan": False,
            "get_recommendations": False,
            "send_email": False,
            "delete_document": False,
            "run_sql": False,
        },
    }
    columns = (*REGISTERED_TOOL_NAMES, *DENIED_CAPABILITIES)
    return tuple((agent, tool, table[agent][tool]) for agent in AGENT_NAMES for tool in columns)


#: ``(agent, tool, allowed)`` for every pair. The test parametrises over this.
PERMISSION_MATRIX: tuple[tuple[str, str, bool], ...] = _rows()

#: Agent -> the tool names it may call, derived from the matrix so it cannot drift.
AGENT_TOOLS: dict[str, frozenset[str]] = {
    agent: frozenset(
        tool
        for matrix_agent, tool, allowed in PERMISSION_MATRIX
        if matrix_agent == agent and allowed
    )
    for agent in AGENT_NAMES
}

#: Tools whose calls the retrieval sub-path answers rather than the tools node.
#: ``search_course`` is deliberately absent: it returns course structure, not
#: passages, so it is executed by the generic ``tools`` node.
RETRIEVAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "search_documents",
        "search_knowledge_graph",
        "search_books",
        "search_web_sources",
    }
)


__all__ = [
    "AGENT_NAMES",
    "AGENT_TOOLS",
    "DENIED_CAPABILITIES",
    "FORBIDDEN_PERMISSIONS",
    "PERMISSION_MATRIX",
    "REGISTERED_TOOL_NAMES",
    "RETRIEVAL_TOOL_NAMES",
]
