"""Loop, cost and deadline guards.

Every bound in ``docs/architecture/agent-architecture.md`` section 9 is checked
**before** a model or tool call, so a breach costs nothing further. The routing
functions are the only gates on the happy path; this module supplies the
arithmetic they share and the vocabulary the exit node records.

Two rules:

* Nothing here raises. A budget breach is a degradation reason, and the turn
  always terminates at ``answer_composer``.
* Nothing here performs I/O. Given a state and settings the answers are total
  functions, which is what lets the loop-bound tests run with a fake gateway and
  no clock control beyond writing ``deadline_ns`` into the state.
"""

from __future__ import annotations

import time

from coursellm.agents.state import (
    ConversationState,
    DegradationReason,
    TokenUsage,
)
from coursellm.core.config import Settings

_NS_PER_MS = 1_000_000


def remaining_ms(state: ConversationState) -> float:
    """Milliseconds left before ``deadline_ns``, possibly negative.

    A state without a deadline is treated as unbounded: the deadline is set once
    at entry, and a missing one means the caller opted out rather than that the
    turn expired at the epoch.
    """
    deadline = state.get("deadline_ns")
    if deadline is None:
        return float("inf")
    return (deadline - time.monotonic_ns()) / _NS_PER_MS


def past_deadline(state: ConversationState) -> bool:
    return remaining_ms(state) <= 0


def iteration_limit_reached(state: ConversationState, settings: Settings) -> bool:
    return state.get("iteration_count", 0) >= settings.graph_max_steps


def tool_limit_reached(state: ConversationState, settings: Settings) -> bool:
    return state.get("tool_call_count", 0) >= settings.agent_max_tool_calls_per_turn


def retrieval_limit_reached(state: ConversationState, settings: Settings) -> bool:
    return state.get("retrieval_pass", 0) >= settings.agent_max_retrieval_passes


def token_budget_exceeded(state: ConversationState, settings: Settings) -> bool:
    usage = state.get("token_usage") or TokenUsage(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0, calls=0
    )
    return usage["total_tokens"] >= settings.agent_max_tokens_per_turn


def cost_budget_exceeded(state: ConversationState, settings: Settings) -> bool:
    usage = state.get("token_usage") or TokenUsage(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0, calls=0
    )
    return usage["cost_usd"] >= settings.agent_max_cost_usd_per_turn


def model_budget_breach(state: ConversationState, settings: Settings) -> DegradationReason | None:
    """Why no further model call may be made, or ``None``.

    Deadline first: a slow provider must not be able to extend the turn, so the
    wall-clock bound outranks the token and cost bounds when several hold.
    """
    if past_deadline(state):
        return DegradationReason.DEADLINE_EXCEEDED
    if token_budget_exceeded(state, settings) or cost_budget_exceeded(state, settings):
        return DegradationReason.TOKEN_BUDGET_EXCEEDED
    if iteration_limit_reached(state, settings):
        return DegradationReason.ITERATION_LIMIT_REACHED
    return None


def can_call_model(state: ConversationState, settings: Settings) -> bool:
    """Whether another model call is admissible."""
    return model_budget_breach(state, settings) is None


def exit_reasons(state: ConversationState, settings: Settings) -> list[DegradationReason]:
    """Every bound the turn has hit, in a stable order.

    Called by ``answer_composer``, which is the single exit for every breach, so
    the reason a bounded turn was shortened is always recorded even though the
    routing functions themselves are pure and cannot write state.
    """
    reasons: list[DegradationReason] = []
    if iteration_limit_reached(state, settings):
        reasons.append(DegradationReason.ITERATION_LIMIT_REACHED)
    if tool_limit_reached(state, settings):
        reasons.append(DegradationReason.TOOL_LIMIT_REACHED)
    if token_budget_exceeded(state, settings) or cost_budget_exceeded(state, settings):
        reasons.append(DegradationReason.TOKEN_BUDGET_EXCEEDED)
    if past_deadline(state):
        reasons.append(DegradationReason.DEADLINE_EXCEEDED)
    return reasons


__all__ = [
    "can_call_model",
    "cost_budget_exceeded",
    "exit_reasons",
    "iteration_limit_reached",
    "model_budget_breach",
    "past_deadline",
    "remaining_ms",
    "retrieval_limit_reached",
    "token_budget_exceeded",
    "tool_limit_reached",
]
