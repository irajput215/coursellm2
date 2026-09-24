"""Grounded quiz generation.

The generator has four behaviours that are not obvious from the happy path, and
each exists because the alternative fails a student.

* **No evidence means no quiz.** Retrieval runs before the model does; when
  nothing is retrieved the draft is a typed failure with a degradation reason
  and zero items. A quiz invented from parametric memory would be graded, and a
  fabricated grade is worse than an explicit failure.
* **A model failure never becomes invented items.** A gateway failure, an
  unparseable structured response, or a response in which no item resolves to a
  retrieved passage each produce the same typed failure, never prose.
* **A short quiz is not padded.** If three of five items are grounded, the draft
  carries three and records the shortfall. Padding to reach ``n_items`` would
  manufacture exactly the ungrounded items the retrieval step exists to prevent.
* **A concept is attached, never invented.** An item's ``concept_id`` is kept
  only when it names a concept in the caller's candidate set *and* at least one
  of the item's supporting passages is linked to that concept through graph
  evidence. Otherwise it is set to ``null``.

The pipeline is the same one the tutor uses — hybrid retrieval, RRF fusion,
cross-encoder reranking, budgeted context assembly with citation ids — so a quiz
item is grounded by exactly the machinery that grounds an answer.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.assessment.schemas import (
    INSUFFICIENT_GROUNDED_ITEMS,
    LLM_UNAVAILABLE,
    MULTIPLE_CHOICE_CRITERION,
    NO_EVIDENCE,
    NO_GROUNDED_ITEMS,
    STRUCTURED_OUTPUT_FAILED,
    Difficulty,
    GeneratedQuiz,
    GeneratedQuizItem,
    ItemType,
    QuizCitation,
    QuizDraft,
    QuizItem,
    RubricCriterion,
    default_rubric,
)
from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError, ServiceUnavailableError, UpstreamError
from coursellm.core.logging import get_logger
from coursellm.db.models.content import Chunk
from coursellm.db.models.graph import Concept, ConceptEdge
from coursellm.db.tenancy import TenantScope
from coursellm.graph.repository import ConceptGraphRepository
from coursellm.llm import ChatMessage, LLMGateway, LLMRequest, ModelTask, resolve_model
from coursellm.llm.cost import count_tokens
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import (
    AssembledContext,
    ChunkPosition,
    DocumentMeta,
    assemble,
)
from coursellm.rag.rerank.pipeline import RankingOutcome, rank
from coursellm.rag.retrieval.hybrid import hybrid_search
from coursellm.rag.retrieval.types import RetrievalFilters
from coursellm.repositories.content import CourseRepository, DocumentRepository

logger = get_logger(__name__)

#: The versioned prompt that carries the item schema and the grounding rules.
QUIZ_TEMPLATE = "assessor.quiz"
#: When the caller does not choose, four-option multiple choice is the default.
DEFAULT_ITEM_TYPES: tuple[ItemType, ...] = ("multiple_choice",)
#: Upper bound on the concept candidates advertised to the model.
_MAX_CANDIDATE_CONCEPTS = 25
#: Upper bound on items requested in one call, mirroring the tool schema.
MAX_ITEMS = 20
#: How much of each supporting passage is persisted for grading. Bounded so a
#: chatty document cannot turn a quiz row into a document store.
_EVIDENCE_QUOTE_CHARS = 1200


async def generate_quiz(
    session: AsyncSession,
    scope: TenantScope,
    settings: Settings,
    gateway: LLMGateway,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    concept_ids: Sequence[uuid.UUID] | None = None,
    n_items: int = 5,
    difficulty: Difficulty | None = None,
    item_types: Sequence[ItemType] | None = None,
) -> QuizDraft:
    """Retrieve evidence and generate a grounded quiz draft for one course.

    Nothing is persisted here: the service owns the write so that the generator
    can be exercised with a scripted gateway and a hand-built context.
    """
    resolved_difficulty: Difficulty = difficulty or "medium"
    resolved_types: list[ItemType] = list(item_types) if item_types else list(DEFAULT_ITEM_TYPES)
    requested = max(1, min(n_items, MAX_ITEMS))

    course = await CourseRepository(session, scope).get_owned(course_id, user_id)
    if course is None:
        msg = "Course not found."
        raise NotFoundError(msg)

    candidates = await _candidate_concepts(
        session, scope, course_id=course_id, concept_ids=concept_ids
    )
    query = _retrieval_query(course.name, candidates)
    semantic, lexical = await hybrid_search(
        session,
        scope,
        settings,
        query=query,
        filters=RetrievalFilters(course_id=course_id),
    )
    ranking = await rank(query=query, semantic=semantic, lexical=lexical, settings=settings)
    model = resolve_model(settings, ModelTask.REASONING)
    assembled = assemble(
        ranking.results,
        documents_by_id=await _documents_by_id(session, scope, ranking),
        settings=settings,
        token_counter=_counter(model),
        chunk_positions=await _chunk_positions(session, scope, ranking),
    )
    linked = await citation_concept_links(
        session, scope, assembled=assembled, candidate_ids=set(candidates)
    )
    draft = await draft_from_context(
        settings=settings,
        gateway=gateway,
        prompts=PromptLibrary(settings),
        assembled=assembled,
        course_id=course_id,
        course_name=course.name,
        concept_candidates=candidates,
        linked_concepts_by_citation=linked,
        n_items=requested,
        difficulty=resolved_difficulty,
        item_types=resolved_types,
    )
    retrieval_reasons = unique([*semantic.degraded, *lexical.degraded, *ranking.degraded])
    draft.degraded = unique([*draft.degraded, *retrieval_reasons])
    draft.concept_ids = list(candidates)
    return draft


async def draft_from_context(
    *,
    settings: Settings,
    gateway: LLMGateway,
    prompts: PromptLibrary,
    assembled: AssembledContext,
    course_id: uuid.UUID,
    course_name: str,
    concept_candidates: Mapping[uuid.UUID, str],
    linked_concepts_by_citation: Mapping[str, AbstractSet[uuid.UUID]],
    n_items: int,
    difficulty: Difficulty,
    item_types: Sequence[ItemType],
) -> QuizDraft:
    """Turn an assembled context into a draft, or into a typed failure.

    Kept separate from :func:`generate_quiz` so the model boundary — the prompt,
    the structured-output schema and the post-validation that rejects an
    ungrounded or malformed item — is testable without a database.
    """
    base = QuizDraft(
        course_id=course_id,
        difficulty=difficulty,
        item_types=list(item_types),
        requested_items=n_items,
    )
    if not assembled.passages:
        base.shortfall = n_items
        base.degraded = [NO_EVIDENCE]
        return base

    system = prompts.render(
        QUIZ_TEMPLATE,
        course_name=course_name,
        evidence=assembled.prompt_text,
    )
    user = _quiz_user_message(n_items, difficulty, item_types, concept_candidates)
    request = LLMRequest(
        task=ModelTask.REASONING,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ],
        temperature=settings.llm_temperature,
        purpose=QUIZ_TEMPLATE,
        response_model=GeneratedQuiz,
    )
    try:
        response = await gateway.complete(request)
    except (ServiceUnavailableError, UpstreamError) as exc:
        logger.warning(
            "quiz_generation_degraded",
            reason=LLM_UNAVAILABLE,
            exc_type=type(exc).__name__,
        )
        base.shortfall = n_items
        base.degraded = [LLM_UNAVAILABLE]
        return base

    generated = _coerce_generated(response.parsed, response.text)
    if generated is None:
        logger.warning("quiz_generation_degraded", reason=STRUCTURED_OUTPUT_FAILED)
        base.shortfall = n_items
        base.degraded = [STRUCTURED_OUTPUT_FAILED]
        return base

    items, reasons = build_quiz_items(
        generated,
        available_citations={citation.citation_id for citation in assembled.citations},
        candidates=concept_candidates,
        linked_by_citation=linked_concepts_by_citation,
        allowed_types=item_types,
        n_items=n_items,
    )
    base.items = items
    base.shortfall = max(0, n_items - len(items))
    degraded = list(reasons)
    if not items:
        degraded.append(NO_GROUNDED_ITEMS)
    elif base.shortfall:
        degraded.append(INSUFFICIENT_GROUNDED_ITEMS)
    base.degraded = unique(degraded)
    content_by_citation = {
        passage.citation_id: passage.content[:_EVIDENCE_QUOTE_CHARS]
        for passage in assembled.passages
    }
    base.citations = [
        QuizCitation(
            citation_id=citation.citation_id,
            chunk_id=citation.chunk_id,
            document_id=citation.document_id,
            filename=citation.filename,
            page=citation.page,
            source_type=citation.source_type.value,
            quote=content_by_citation.get(citation.citation_id, citation.quote),
        )
        for citation in assembled.citations
    ]
    return base


def build_quiz_items(
    generated: GeneratedQuiz,
    *,
    available_citations: AbstractSet[str],
    candidates: Mapping[uuid.UUID, str],
    linked_by_citation: Mapping[str, AbstractSet[uuid.UUID]],
    allowed_types: Sequence[ItemType],
    n_items: int,
) -> tuple[list[QuizItem], list[str]]:
    """Validate and ground each generated item, dropping the invalid ones.

    Returns the accepted items (at most ``n_items``) and one degradation reason
    per dropped item class. An item is dropped, never repaired: a multiple-choice
    item with two correct options has no unambiguous grade, and an item whose
    citations do not resolve is not grounded.
    """
    allowed = set(allowed_types)
    items: list[QuizItem] = []
    reasons: list[str] = []

    for raw in generated.items:
        if len(items) >= n_items:
            break
        if raw.item_type not in allowed:
            reasons.append("unsupported_item_type")
            continue

        citation_ids = _resolvable_citations(raw.citation_ids, available_citations)
        if not citation_ids:
            reasons.append("ungrounded_item")
            continue

        correct_index = _correct_index(raw)
        if raw.item_type == "multiple_choice" and correct_index is None:
            reasons.append("invalid_multiple_choice")
            continue
        if raw.item_type == "short_answer" and not raw.model_answer:
            reasons.append("missing_model_answer")
            continue
        if raw.item_type == "concept_check" and raw.correct_boolean is None:
            reasons.append("missing_correct_boolean")
            continue

        try:
            item = QuizItem(
                item_id=f"item-{len(items) + 1}",
                item_type=raw.item_type,
                prompt=raw.prompt.strip() or "Untitled item",
                choices=list(raw.choices),
                correct_choice_index=correct_index,
                model_answer=raw.model_answer,
                correct_boolean=raw.correct_boolean,
                rubric=_rubric_for(raw.item_type),
                citation_ids=citation_ids,
                concept_id=_linked_concept(
                    raw.concept_id or "", citation_ids, candidates, linked_by_citation
                ),
                justification=raw.justification,
            )
        except PydanticValidationError:
            reasons.append("invalid_item")
            continue
        items.append(item)

    return items, unique(reasons)


async def citation_concept_links(
    session: AsyncSession,
    scope: TenantScope,
    *,
    assembled: AssembledContext,
    candidate_ids: AbstractSet[uuid.UUID],
) -> dict[str, set[uuid.UUID]]:
    """Map each citation id to the candidate concepts its passage is linked to.

    A passage is linked to a concept when a graph edge records its chunk or its
    document as provenance. That is the only evidence this schema has that a
    passage is *about* a concept, so it is the only basis on which a concept may
    be attached to an item.
    """
    if not assembled.passages or not candidate_ids:
        return {citation.citation_id: set() for citation in assembled.citations}

    chunk_ids = [passage.chunk_id for passage in assembled.passages]
    document_ids = list({passage.document_id for passage in assembled.passages})
    stmt = select(
        ConceptEdge.source_concept_id,
        ConceptEdge.target_concept_id,
        ConceptEdge.provenance_chunk_id,
        ConceptEdge.provenance_document_id,
    ).where(
        ConceptEdge.tenant_id == scope.tenant_id,
        or_(
            ConceptEdge.provenance_chunk_id.in_(chunk_ids),
            ConceptEdge.provenance_document_id.in_(document_ids),
        ),
    )
    by_chunk: dict[uuid.UUID, set[uuid.UUID]] = {}
    by_document: dict[uuid.UUID, set[uuid.UUID]] = {}
    for source, target, chunk_id, document_id in (await session.execute(stmt)).all():
        for concept_id in (source, target):
            if concept_id not in candidate_ids:
                continue
            if chunk_id is not None:
                by_chunk.setdefault(chunk_id, set()).add(concept_id)
            if document_id is not None:
                by_document.setdefault(document_id, set()).add(concept_id)

    links: dict[str, set[uuid.UUID]] = {}
    for passage in assembled.passages:
        linked = set(by_chunk.get(passage.chunk_id, set()))
        linked.update(by_document.get(passage.document_id, set()))
        links[passage.citation_id] = linked
    return links


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _coerce_generated(parsed: object, text: str) -> GeneratedQuiz | None:
    """Accept a validated schema instance, or parse the raw text as a fallback."""
    if isinstance(parsed, GeneratedQuiz):
        return parsed
    if not text:
        return None
    try:
        return GeneratedQuiz.model_validate_json(text)
    except PydanticValidationError:
        return None


def _resolvable_citations(raw_ids: Sequence[str], available: AbstractSet[str]) -> list[str]:
    """Keep only citation ids that resolve, in first-appearance order."""
    seen: set[str] = set()
    result: list[str] = []
    for citation_id in raw_ids:
        if citation_id in available and citation_id not in seen:
            seen.add(citation_id)
            result.append(citation_id)
    return result


def _correct_index(raw: GeneratedQuizItem) -> int | None:
    """The one valid correct index, or ``None`` when the item is ambiguous."""
    if len(raw.choices) != 4 or len(raw.correct_choice_indices) != 1:
        return None
    index = raw.correct_choice_indices[0]
    return index if 0 <= index < 4 else None


def _rubric_for(item_type: ItemType) -> list[RubricCriterion]:
    if item_type == "multiple_choice":
        return [RubricCriterion(criterion=MULTIPLE_CHOICE_CRITERION, weight=1.0)]
    return default_rubric()


def _linked_concept(
    raw_concept_id: str,
    citation_ids: Sequence[str],
    candidates: Mapping[uuid.UUID, str],
    linked_by_citation: Mapping[str, AbstractSet[uuid.UUID]],
) -> uuid.UUID | None:
    """The item's concept, only when it exists in the tenant and the evidence links it."""
    if not raw_concept_id:
        return None
    try:
        concept_id = uuid.UUID(raw_concept_id)
    except (ValueError, AttributeError):
        return None
    if concept_id not in candidates:
        return None
    linked: set[uuid.UUID] = set()
    for citation_id in citation_ids:
        linked.update(linked_by_citation.get(citation_id, frozenset()))
    return concept_id if concept_id in linked else None


async def _candidate_concepts(
    session: AsyncSession,
    scope: TenantScope,
    *,
    course_id: uuid.UUID,
    concept_ids: Sequence[uuid.UUID] | None,
) -> dict[uuid.UUID, str]:
    """The concepts an item may be attached to.

    Explicit ids are validated against the tenant's graph rather than trusted;
    an id that does not resolve is skipped, so a caller cannot name a concept
    that only exists in another tenant.
    """
    repo = ConceptGraphRepository(session, scope)
    resolved: dict[uuid.UUID, str] = {}
    if concept_ids:
        for concept_id in concept_ids:
            concept = await repo.get(concept_id)
            if concept is not None:
                resolved[concept.id] = concept.name
        return resolved

    stmt = (
        select(Concept)
        .where(Concept.tenant_id == scope.tenant_id, Concept.course_id == course_id)
        .order_by(Concept.difficulty, Concept.name)
        .limit(_MAX_CANDIDATE_CONCEPTS)
    )
    for concept in (await session.execute(stmt)).scalars():
        resolved[concept.id] = concept.name
    return resolved


async def _documents_by_id(
    session: AsyncSession, scope: TenantScope, ranking: RankingOutcome
) -> dict[uuid.UUID, DocumentMeta]:
    """Load citation metadata through the repository, never with ad-hoc SQL."""
    documents = DocumentRepository(session, scope)
    loaded: dict[uuid.UUID, DocumentMeta] = {}
    for passage in ranking.results:
        document_id = passage.source.document_id
        if document_id in loaded:
            continue
        document = await documents.get(document_id)
        if document is None:
            continue
        loaded[document_id] = DocumentMeta(
            document_id=document.id,
            filename=document.filename,
            source_type=document.source_type,
            content_type=document.content_type,
            page_count=document.page_count,
        )
    return loaded


async def _chunk_positions(
    session: AsyncSession, scope: TenantScope, ranking: RankingOutcome
) -> dict[uuid.UUID, ChunkPosition]:
    """Load chunk indices so adjacent chunks can be merged into continuous prose."""
    chunk_ids = [passage.chunk_id for passage in ranking.results]
    if not chunk_ids:
        return {}
    stmt = select(Chunk.id, Chunk.chunk_index, Chunk.starts_mid_sentence).where(
        Chunk.tenant_id == scope.tenant_id,
        Chunk.id.in_(chunk_ids),
    )
    rows = (await session.execute(stmt)).all()
    return {
        row.id: ChunkPosition(index=row.chunk_index, starts_mid_sentence=row.starts_mid_sentence)
        for row in rows
    }


def _retrieval_query(course_name: str, candidates: Mapping[uuid.UUID, str]) -> str:
    names = " ".join(sorted(candidates.values()))
    return f"{course_name} {names}".strip()[:2000]


def _quiz_user_message(
    n_items: int,
    difficulty: Difficulty,
    item_types: Sequence[ItemType],
    candidates: Mapping[uuid.UUID, str],
) -> str:
    """The item parameters the prompt template deliberately does not interpolate."""
    candidate_rows = json.dumps(
        [{"concept_id": str(concept_id), "name": name} for concept_id, name in candidates.items()],
        sort_keys=True,
    )
    return (
        f"Generate {n_items} items at {difficulty} difficulty.\n"
        f"Allowed item types: {', '.join(item_types)}.\n"
        f"Candidate concepts: {candidate_rows}\n"
        "Use a candidate concept id only when the supporting passages are about it; "
        "otherwise set concept_id to null. Do not invent a concept id."
    )


def _counter(model: str) -> Callable[[str], int]:
    """The same token counter the gateway prices with, bound to the task's model."""
    return lambda text: count_tokens(text, model)


def unique(values: Sequence[str]) -> list[str]:
    """De-duplicate strings while preserving their first-appearance order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "DEFAULT_ITEM_TYPES",
    "MAX_ITEMS",
    "QUIZ_TEMPLATE",
    "build_quiz_items",
    "citation_concept_links",
    "draft_from_context",
    "generate_quiz",
    "unique",
]
