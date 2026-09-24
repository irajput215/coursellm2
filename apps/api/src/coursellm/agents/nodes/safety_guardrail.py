"""``safety_guardrail`` — the last line before the answer reaches the student.

Two controls, both structural rather than prompt-level:

* **Instruction-region content fails closed.** If the output contains an
  instruction-override pattern or an attempt to reproduce the evidence fence, the
  guardrail does not try to salvage the prose: it discards it and emits
  evidence-only output built from typed state, and records
  ``safety_guardrail_error`` so the turn is visible for review.
* **Secrets are stripped.** A model that echoes a credential-shaped string has
  it masked before the text is returned, whether or not the turn was flagged.

It also re-checks citations against the passages that were actually available and
counts any that do not resolve, so a hallucinated id that survived the composer
still cannot reach the client.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from coursellm.agents.nodes import NodeFn
from coursellm.agents.state import (
    AgentError,
    ConversationState,
    DegradationReason,
    merge_evaluation_metadata,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.rag.generation.citations import (
    extract_citation_ids,
    strip_hallucinated,
)

logger = get_logger(__name__)

SAFETY_GUARDRAIL_NODE = "safety_guardrail"

#: Phrases that mark an instruction-override attempt. Deliberately pattern-based:
#: a detector that must not fail open cannot be a model call.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+|any\s+)?(previous|prior|above)", re.I),
    re.compile(r"system\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"</?untrusted_evidence", re.I),
    re.compile(r"new\s+instructions\s*:", re.I),
)

#: Credential-shaped strings, masked wherever they appear.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*\S+"),
)

_SECRET_MASK = "[redacted-secret]"  # noqa: S105 - a mask literal, not a credential
#: How much of a passage the evidence-only fallback quotes.
_EVIDENCE_CHARS = 400


def make_safety_guardrail_node(*, settings: Settings) -> NodeFn:
    """Build the guardrail node."""

    async def safety_guardrail(state: ConversationState) -> dict[str, Any]:
        text = _last_ai_text(state)
        errors: list[AgentError] = []
        degraded: list[DegradationReason] = []

        if _has_instruction_region(text):
            logger.warning("safety_guardrail_instruction_region")
            text = _evidence_only(state)
            degraded.append(DegradationReason.SAFETY_GUARDRAIL_ERROR)
            errors.append(
                AgentError(
                    node=SAFETY_GUARDRAIL_NODE,
                    error_type="InstructionRegionDetected",
                    message=(
                        "The model output contained instruction-shaped content; the "
                        "turn was reduced to evidence-only output."
                    ),
                    retryable=False,
                )
            )

        text, secret_count = _strip_secrets(text)
        if secret_count:
            logger.warning("safety_guardrail_secret_stripped", count=secret_count)
            errors.append(
                AgentError(
                    node=SAFETY_GUARDRAIL_NODE,
                    error_type="SecretInOutput",
                    message=f"Masked {secret_count} credential-shaped string(s).",
                    retryable=False,
                )
            )

        available = _available_citations(state)
        cleaned, removed = strip_hallucinated(text, available)
        metadata = merge_evaluation_metadata(
            state,
            citation_hallucinations=(_hallucination_count(state) + len(removed)),
        )
        draft = {
            "text": cleaned,
            "grounded": bool(extract_citation_ids(cleaned))
            and not _has_instruction_region(cleaned),
            "citation_ids": extract_citation_ids(cleaned),
            "degraded": [reason.value for reason in degraded],
            "flagged": bool(degraded),
        }
        return validate_update(
            {
                "answer_draft": draft,
                "errors": errors,
                "degraded": degraded,
                "evaluation_metadata": metadata,
            }
        )

    return safety_guardrail


def _last_ai_text(state: ConversationState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            return message.content
    draft = state.get("answer_draft")
    if isinstance(draft, dict):
        value = draft.get("text")
        if isinstance(value, str):
            return value
    return ""


def _hallucination_count(state: ConversationState) -> int:
    metadata = state.get("evaluation_metadata")
    if not metadata:
        return 0
    return int(metadata.get("citation_hallucinations", 0))


def _has_instruction_region(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def _strip_secrets(text: str) -> tuple[str, int]:
    count = 0
    for pattern in _SECRET_PATTERNS:
        text, replaced = pattern.subn(_SECRET_MASK, text)
        count += replaced
    return text, count


def _evidence_only(state: ConversationState) -> str:
    documents = state.get("retrieved_documents") or []
    if not documents:
        return (
            "I cannot safely answer that from the available material. Please rephrase the question."
        )
    document = documents[0]
    snippet = " ".join(document["content"].split())[:_EVIDENCE_CHARS]
    return f"[{document['citation_id']}] {snippet}"


def _available_citations(state: ConversationState) -> set[str]:
    available = {citation["citation_id"] for citation in state.get("citations") or []}
    available.update(document["citation_id"] for document in state.get("retrieved_documents") or [])
    return available


__all__ = ["SAFETY_GUARDRAIL_NODE", "make_safety_guardrail_node"]
