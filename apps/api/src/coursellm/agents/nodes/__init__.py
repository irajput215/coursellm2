"""One module per graph node.

Every node is a plain ``async (state) -> partial_state`` function, built by a
``make_*`` factory that closes over its dependencies. Two consequences are
deliberate:

* a node can be unit-tested with a fake gateway and no graph, and
* the node's dependency surface is visible in its factory signature rather than
  discovered through an import.

Factories return the node; the node returns only the keys its contract declares,
and :func:`coursellm.agents.state.validate_update` is available to assert that in
tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from coursellm.agents.state import ConversationState

NodeFn = Callable[[ConversationState], Awaitable[dict[str, Any]]]

__all__ = ["NodeFn"]
