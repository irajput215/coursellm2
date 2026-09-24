"""``answer_composer`` — phrase a decision that was already made and validated.

The composer never decides what is true. It receives typed evidence (retrieved
passages, graph entities), typed artifacts (a roadmap, recommendations, a quiz)
and possibly a draft, and produces prose plus verified citations. It reuses
:class:`~coursellm.rag.generation.generator.AnswerGenerator` for the tutor path,
so citation verification, the refusal template and the extractive fallback are the
same code the non-agent chat path uses rather than a second implementation.

It is also the single exit for every bound in section 9: a breached turn still
produces an answer (or an explicit refusal) and records why it was shortened.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from coursellm.agents import budgets
from coursellm.agents.nodes import NodeFn
from coursellm.agents.routing import assess_grounding
from coursellm.agents.state import (
    Citation,
    ConversationState,
    DegradationReason,
    RetrievedDocument,
    add_usage,
    merge_evaluation_metadata,
    message_history,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.db.models.content import SourceType
from coursellm.llm import LLMGateway, ModelTask, resolve_model
from coursellm.llm.cost import count_tokens
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.citations import (
    is_refusal,
    strip_hallucinated,
    verify_citations,
)
from coursellm.rag.generation.context import (
    AssembledContext,
    DocumentMeta,
    assemble,
)
from coursellm.rag.generation.generator import AnswerGenerator, GeneratedAnswer, compose_extractive
from coursellm.rag.rerank.pipeline import RankedPassage
from coursellm.rag.retrieval.types import SearchResult

logger = get_logger(__name__)

ANSWER_COMPOSER_NODE = "answer_composer"

MetadataProvider = Callable[[ConversationState], Awaitable[Mapping[uuid.UUID, DocumentMeta]]]
CourseNameProvider = Callable[[ConversationState], Awaitable[str]]


def make_answer_composer_node(
    *,
    settings: Settings,
    gateway: LLMGateway,
    prompts: PromptLibrary,
    metadata_provider: MetadataProvider | None = None,
    course_name_provider: CourseNameProvider | None = None,
) -> NodeFn:
    """Build the composer node."""
    generator = AnswerGenerator(settings, gateway, prompts)
    model = resolve_model(settings, ModelTask.TUTORING)

    async def answer_composer(state: ConversationState) -> dict[str, Any]:
        degraded: list[DegradationReason] = list(budgets.exit_reasons(state, settings))
        question = _question(state)
        course_name = (
            await course_name_provider(state) if course_name_provider is not None else "your course"
        )
        metas = await metadata_provider(state) if metadata_provider is not None else {}
        assembled = _assemble(state, settings=settings, model=model, metas=metas)

        draft_text = _draft_text(state)
        usage = state.get("token_usage")
        model_name: str | None = None
        prompt_version: str | None = None

        if draft_text is not None:
            text, grounded, hallucinated, used_ids = _finalise_draft(draft_text, assembled)
        elif budgets.model_budget_breach(state, settings) is not None:
            text, grounded, hallucinated, used_ids = _without_model(
                state, assembled, question=question, course_name=course_name, prompts=prompts
            )
        elif state.get("intent", "tutor") != "tutor":
            text, grounded, hallucinated, used_ids = _from_artifacts(state)
        else:
            answer = await generator.answer(
                question,
                assembled,
                course_name=course_name,
                history=message_history(state, limit=settings.history_window_messages),
            )
            text = answer.text
            grounded = answer.grounded
            hallucinated = 0
            used_ids = [citation.citation_id for citation in answer.citations]
            degraded.extend(_map_generated(answer))
            if answer.usage is not None:
                usage = add_usage(
                    usage,
                    prompt_tokens=answer.usage.prompt_tokens,
                    completion_tokens=answer.usage.completion_tokens,
                    total_tokens=answer.usage.total_tokens,
                    cost_usd=answer.usage.cost_usd,
                )
                model_name = answer.model
            prompt_version = answer.prompt_template_id

        if not text.strip():
            text = _fallback_text(state)
        grounding = assess_grounding(state, settings)
        citations = _citations(assembled, used_ids)
        metadata = merge_evaluation_metadata(
            state,
            grounded=grounded,
            citation_hallucinations=(
                int(_metadata(state).get("citation_hallucinations") or 0) + hallucinated
            ),
            models={
                **(_metadata(state).get("models") or {}),
                state.get("intent", "tutor"): model_name or settings.resolved_agent_model,
            },
            prompt_versions={
                **(_metadata(state).get("prompt_versions") or {}),
                **(
                    {state.get("intent", "tutor"): prompt_version}
                    if prompt_version is not None
                    else {}
                ),
            },
        )
        draft = {
            "text": text,
            "grounded": grounded,
            "citation_ids": [citation["citation_id"] for citation in citations],
            "degraded": [reason.value for reason in degraded],
        }
        update = {
            "messages": [AIMessage(content=text)],
            "citations": citations,
            "grounding": {
                "grounded": grounded,
                "evidence_count": grounding["evidence_count"],
                "max_rerank_score": grounding["max_rerank_score"],
            },
            "evaluation_metadata": metadata,
            "answer_draft": draft,
            "token_usage": usage,
            "degraded": degraded,
        }
        return validate_update(update)

    return answer_composer


# ---------------------------------------------------------------------------
# Draft and no-model paths
# ---------------------------------------------------------------------------
def _finalise_draft(text: str, assembled: AssembledContext) -> tuple[str, bool, int, list[str]]:
    available = {citation.citation_id for citation in assembled.citations}
    cleaned, removed = strip_hallucinated(text, available)
    report = verify_citations(cleaned, available)
    grounded = bool(report.valid) or is_refusal(cleaned)
    return cleaned, grounded, len(removed) + len(report.hallucinated), report.valid


def _without_model(
    state: ConversationState,
    assembled: AssembledContext,
    *,
    question: str,
    course_name: str,
    prompts: PromptLibrary,
) -> tuple[str, bool, int, list[str]]:
    """Compose without any further model call, from whatever evidence exists."""
    if assembled.passages:
        available = {citation.citation_id for citation in assembled.citations}
        raw = compose_extractive(assembled, prompts, question=question, course_name=course_name)
        cleaned, removed = strip_hallucinated(raw, available)
        report = verify_citations(cleaned, available)
        return cleaned, bool(report.valid), len(removed), report.valid
    # No evidence: the refusal template, composed from typed state, needs no model.
    text = prompts.render("tutor.refusal", course_name=course_name, question=question)
    return text, False, 0, []


def _from_artifacts(state: ConversationState) -> tuple[str, bool, int, list[str]]:
    """Deterministic phrasing for the non-prose artifacts.

    A roadmap, a ranked catalogue list and a mastery summary are *already*
    decisions; phrasing them deterministically keeps the composer from turning a
    typed artifact back into a prompt the model could contradict.
    """
    intent = state.get("intent", "tutor")
    if intent == "planner":
        roadmap = state.get("roadmap") or {}
        steps = roadmap.get("steps") or []
        if not steps:
            return (
                "I could not build a roadmap from the available course structure yet.",
                False,
                0,
                [],
            )
        lines = ["Here is a suggested order for your goal:"]
        lines.extend(
            f"{index}. {step.get('title', step.get('concept_id', 'Step'))}"
            for index, step in enumerate(steps, start=1)
        )
        return "\n".join(lines), False, 0, []
    if intent == "recommender":
        items = state.get("recommendations") or []
        if not items:
            return (
                "I could not find catalogue resources for that gap yet.",
                False,
                0,
                [],
            )
        lines = ["Resources that match your goal:"]
        lines.extend(
            f"- {item.get('title', item.get('resource_id', 'Resource'))}" for item in items
        )
        return "\n".join(lines), False, 0, []
    if intent == "assessment":
        quiz = state.get("quiz") or {}
        items = quiz.get("items") or []
        if not items:
            return (
                "I could not generate quiz items for this topic. Please try again.",
                False,
                0,
                [],
            )
        lines = ["Here are practice items:"]
        lines.extend(
            f"{index}. {item.get('prompt', 'Item')}" for index, item in enumerate(items, start=1)
        )
        return "\n".join(lines), False, 0, []
    if intent == "progress":
        return _progress_summary(state), False, 0, []
    return _fallback_text(state), False, 0, []


def _progress_summary(state: ConversationState) -> str:
    progress: Mapping[str, Any] = state.get("student_progress") or {}
    mastery = progress.get("mastery") or {}
    weak = progress.get("weak_concepts") or []
    if not mastery and not weak:
        return (
            "I do not have recorded progress for you yet. Complete a quiz or ask a "
            "course question and I will start tracking it."
        )
    lines = ["Here is your current progress:"]
    for concept_id, value in list(mastery.items())[:20]:
        lines.append(f"- {concept_id}: mastery {float(value):.2f}")
    if weak:
        lines.append("Concepts to revise: " + ", ".join(weak[:20]) + ".")
    return "\n".join(lines)


def _fallback_text(state: ConversationState) -> str:
    documents = state.get("retrieved_documents") or []
    if documents:
        top = documents[0]
        snippet = " ".join(top["content"].split())[:280]
        return f"[{top['citation_id']}] {snippet}"
    return "I don't have enough information in your course materials to answer that question."


def _map_generated(answer: GeneratedAnswer) -> list[DegradationReason]:
    mapped: list[DegradationReason] = []
    for reason in answer.degraded:
        if reason == "no_evidence":
            mapped.append(DegradationReason.RETRIEVAL_EMPTY)
        elif reason == "llm_unavailable":
            mapped.append(DegradationReason.LLM_UNAVAILABLE)
    return mapped


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _assemble(
    state: ConversationState,
    *,
    settings: Settings,
    model: str,
    metas: Mapping[uuid.UUID, DocumentMeta],
) -> AssembledContext:
    documents = _dedupe(state.get("retrieved_documents") or [])
    results = _rank(documents)
    documents_by_id: dict[uuid.UUID, DocumentMeta] = dict(metas)
    for document in documents:
        document_id = uuid.UUID(document["document_id"])
        if document_id not in documents_by_id:
            documents_by_id[document_id] = DocumentMeta(
                document_id=document_id,
                filename=f"document {document_id}",
                source_type=_source_type(document["source_type"]),
            )
    return assemble(
        results,
        documents_by_id=documents_by_id,
        settings=settings,
        token_counter=lambda text: count_tokens(text, model),
    )


def _dedupe(documents: Sequence[RetrievedDocument]) -> list[RetrievedDocument]:
    """Collapse append-reducer duplicates, keeping the last (scored) copy."""
    by_id: dict[str, RetrievedDocument] = {}
    for document in documents:
        by_id[document["chunk_id"]] = document
    return list(by_id.values())


def _rank(documents: Sequence[RetrievedDocument]) -> list[RankedPassage]:
    ordered = sorted(
        documents,
        key=lambda document: (
            document.get("rerank_score") is None,
            -(document.get("rerank_score") or 0.0),
            -document.get("rrf_score", 0.0),
        ),
    )
    results: list[RankedPassage] = []
    for final_rank, document in enumerate(ordered, start=1):
        results.append(
            RankedPassage(
                chunk_id=uuid.UUID(document["chunk_id"]),
                final_rank=final_rank,
                rrf_score=document.get("rrf_score", 0.0),
                rerank_score=document.get("rerank_score"),
                semantic_rank=document.get("semantic_rank"),
                lexical_rank=document.get("lexical_rank"),
                source=SearchResult(
                    chunk_id=uuid.UUID(document["chunk_id"]),
                    document_id=uuid.UUID(document["document_id"]),
                    course_id=uuid.UUID(int=0),
                    content=document["content"],
                    page=document["page"],
                    topic=document["topic"],
                    token_count=max(len(document["content"].split()), 1),
                    source_type=_source_type(document["source_type"]),
                    rank=final_rank,
                    score=document.get("rrf_score", 0.0),
                    retriever="semantic",
                ),
            )
        )
    return results


def _citations(assembled: AssembledContext, used_ids: Sequence[str]) -> list[Citation]:
    if not used_ids:
        return []
    by_id = {citation.citation_id: citation for citation in assembled.citations}
    return [
        Citation(
            citation_id=citation.citation_id,
            chunk_id=str(citation.chunk_id),
            document_id=str(citation.document_id),
            page=citation.page,
            source_type=citation.source_type.value,
        )
        for citation_id in used_ids
        if citation_id in by_id
        for citation in [by_id[citation_id]]
    ]


def _source_type(value: str) -> SourceType:
    try:
        return SourceType(value)
    except ValueError:
        return SourceType.OTHER


def _question(state: ConversationState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _draft_text(state: ConversationState) -> str | None:
    draft = state.get("answer_draft")
    if isinstance(draft, dict):
        text = draft.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _metadata(state: ConversationState) -> dict[str, Any]:
    value = state.get("evaluation_metadata")
    return dict(value) if value else {}


__all__ = [
    "ANSWER_COMPOSER_NODE",
    "CourseNameProvider",
    "MetadataProvider",
    "make_answer_composer_node",
]
