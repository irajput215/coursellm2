"""The chat use case: question in, grounded and persisted answer out.

The flow is fixed and lives here rather than in the router so that it is
reachable from the CLI, a worker or a test without HTTP:

    resolve conversation -> hybrid retrieval -> rank -> load document metadata
    -> assemble a budgeted context -> generate -> persist the turn

Two rules are enforced at this level.

* **The question is validated before any retrieval happens.** An over-long query
  is rejected with a domain ``ValidationError`` rather than being embedded and
  searched.
* **A refused question is still conversation history.** The user and assistant
  messages are written in the same transaction whether or not evidence was
  found, so the transcript is complete and auditable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError, ValidationError
from coursellm.core.logging import get_logger
from coursellm.db.models.content import Chunk
from coursellm.db.models.conversation import Conversation, Message, MessageRole
from coursellm.db.tenancy import TenantContext
from coursellm.llm import ChatMessage, LLMGateway, LLMResponse, ModelTask, resolve_model
from coursellm.llm.cost import count_tokens
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import (
    AssembledContext,
    ChunkPosition,
    Citation,
    DocumentMeta,
    assemble,
)
from coursellm.rag.generation.generator import AnswerGenerator, GeneratedAnswer
from coursellm.rag.rerank.pipeline import RankingOutcome, rank
from coursellm.rag.retrieval.hybrid import hybrid_search
from coursellm.rag.retrieval.types import RetrievalFilters
from coursellm.repositories.content import CourseRepository, DocumentRepository

logger = get_logger(__name__)

# How many prior turns are replayed to the model. Bounded so that a long
# conversation cannot crowd out the evidence it is supposed to ground the answer
# in; the evidence budget is the one that must be protected.
_HISTORY_LIMIT = 6
# Conversation titles are derived from the opening question.
_TITLE_CHARS = 120


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    """The result of one chat turn."""

    answer: str
    citations: list[Citation]
    grounded: bool
    degraded: list[str]
    conversation_id: uuid.UUID
    usage: LLMResponse | None


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    """The retrieval half of a turn, before the model is called.

    Kept as a separate step so the streaming route can reuse exactly the same
    retrieval, assembly and persistence path as the non-streaming one.
    """

    conversation_id: uuid.UUID
    question: str
    course_name: str
    assembled: AssembledContext
    degraded: list[str]
    history: list[ChatMessage]


async def ask(
    session: AsyncSession,
    settings: Settings,
    gateway: LLMGateway,
    context: TenantContext,
    *,
    question: str,
    course_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> ChatAnswer:
    """Answer ``question`` for the authenticated caller and persist the turn."""
    prepared = await prepare_turn(
        session,
        settings,
        context,
        question=question,
        course_id=course_id,
        conversation_id=conversation_id,
    )
    generated = await build_generator(settings, gateway).answer(
        question,
        prepared.assembled,
        course_name=prepared.course_name,
        history=prepared.history,
    )
    degraded = unique([*prepared.degraded, *generated.degraded])
    await persist_assistant_message(
        session,
        settings,
        context,
        prepared=prepared,
        generated=generated,
        degraded=degraded,
    )
    return ChatAnswer(
        answer=generated.text,
        citations=generated.citations,
        grounded=generated.grounded,
        degraded=degraded,
        conversation_id=prepared.conversation_id,
        usage=generated.usage,
    )


async def prepare_turn(
    session: AsyncSession,
    settings: Settings,
    context: TenantContext,
    *,
    question: str,
    course_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> PreparedTurn:
    """Retrieve, rank, assemble and persist the user message for one turn."""
    validate_question(settings, question)
    conversation = await _resolve_conversation(
        session,
        context,
        question=question,
        course_id=course_id,
        conversation_id=conversation_id,
    )
    course_name = await _course_name(session, context, course_id=course_id)
    history = await _load_history(session, context, conversation_id=conversation.id)

    semantic, lexical = await hybrid_search(
        session,
        context,
        settings,
        query=question,
        filters=RetrievalFilters(course_id=course_id),
    )
    ranking = await rank(
        query=question,
        semantic=semantic,
        lexical=lexical,
        settings=settings,
    )

    model = resolve_model(settings, ModelTask.TUTORING)
    counter = _counter(model)
    documents_by_id = await _documents_by_id(session, context, ranking)
    chunk_positions = await _chunk_positions(session, context, ranking)
    assembled = assemble(
        ranking.results,
        documents_by_id=documents_by_id,
        settings=settings,
        token_counter=counter,
        chunk_positions=chunk_positions,
    )

    session.add(
        Message(
            tenant_id=context.tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
            citations=[],
            degraded=[],
            grounded=False,
            token_count=counter(question),
            retrieval_config_version=settings.retrieval_config_version,
            # ``created_at`` is set explicitly rather than left to the server
            # default: both messages of a turn are written in one transaction, and
            # ``now()`` is the transaction timestamp, so server defaults would
            # make the user and assistant rows indistinguishable in time and the
            # transcript would not be reproducibly ordered.
            created_at=_utcnow(),
        )
    )
    await session.flush()

    return PreparedTurn(
        conversation_id=conversation.id,
        question=question,
        course_name=course_name,
        assembled=assembled,
        degraded=unique([*semantic.degraded, *lexical.degraded, *ranking.degraded]),
        history=history,
    )


def build_generator(settings: Settings, gateway: LLMGateway) -> AnswerGenerator:
    """Construct the generator with the settings' prompt library."""
    return AnswerGenerator(settings, gateway, PromptLibrary(settings))


async def persist_assistant_message(
    session: AsyncSession,
    settings: Settings,
    context: TenantContext,
    *,
    prepared: PreparedTurn,
    generated: GeneratedAnswer,
    degraded: Sequence[str],
) -> Message:
    """Persist the assistant message, citations and degradation in this transaction."""
    model = resolve_model(settings, ModelTask.TUTORING)
    token_count = (
        generated.usage.completion_tokens
        if generated.usage is not None
        else count_tokens(generated.text, model)
    )
    message = Message(
        tenant_id=context.tenant_id,
        conversation_id=prepared.conversation_id,
        role=MessageRole.ASSISTANT,
        content=generated.text,
        citations=[citation.model_dump(mode="json") for citation in generated.citations],
        degraded=list(degraded),
        grounded=generated.grounded,
        token_count=token_count,
        retrieval_config_version=settings.retrieval_config_version,
        created_at=_utcnow(),
    )
    session.add(message)
    await session.flush()
    return message


def validate_question(settings: Settings, question: str) -> None:
    """Reject an over-long question before it reaches retrieval."""
    if len(question) > settings.max_query_chars:
        raise ValidationError(
            f"The question is {len(question)} characters long, which exceeds the "
            f"maximum of {settings.max_query_chars}."
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _resolve_conversation(
    session: AsyncSession,
    context: TenantContext,
    *,
    question: str,
    course_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    """Load the caller's conversation, or start a new one.

    A conversation id belonging to another tenant or another user is reported as
    not found. Reporting it as forbidden would confirm that it exists.
    """
    if conversation_id is not None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == context.tenant_id,
            Conversation.user_id == context.user_id,
        )
        conversation = (await session.execute(statement)).scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    conversation = Conversation(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        course_id=course_id,
        title=_title_from(question),
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def _course_name(
    session: AsyncSession, context: TenantContext, *, course_id: uuid.UUID | None
) -> str:
    if course_id is None:
        return "your course"
    course = await CourseRepository(session, context).get_owned(course_id, context.user_id)
    if course is None:
        raise NotFoundError("Course not found.")
    return course.name


async def _load_history(
    session: AsyncSession, context: TenantContext, *, conversation_id: uuid.UUID
) -> list[ChatMessage]:
    """The most recent turns, oldest first, excluding system and tool messages."""
    statement = (
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.conversation_id == conversation_id,
            Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(_HISTORY_LIMIT)
    )
    rows = list((await session.execute(statement)).scalars().all())
    rows.reverse()
    history: list[ChatMessage] = []
    for message in rows:
        role = "user" if message.role is MessageRole.USER else "assistant"
        history.append(ChatMessage(role=role, content=message.content))
    return history


async def _documents_by_id(
    session: AsyncSession, context: TenantContext, ranking: RankingOutcome
) -> dict[uuid.UUID, DocumentMeta]:
    """Load citation metadata through the repository, never with ad-hoc SQL."""
    documents = DocumentRepository(session, context)
    result: dict[uuid.UUID, DocumentMeta] = {}
    for document_id in {passage.source.document_id for passage in ranking.results}:
        document = await documents.get(document_id)
        if document is None:
            continue
        result[document_id] = DocumentMeta(
            document_id=document.id,
            filename=document.filename,
            source_type=document.source_type,
            content_type=document.content_type,
            page_count=document.page_count,
        )
    return result


async def _chunk_positions(
    session: AsyncSession, context: TenantContext, ranking: RankingOutcome
) -> dict[uuid.UUID, ChunkPosition]:
    """Load chunk indices so adjacent chunks can be merged into continuous prose.

    ``SearchResult`` carries the passage text but not its position inside the
    document, so adjacency is resolved here, where a session is available.
    """
    chunk_ids = [passage.chunk_id for passage in ranking.results]
    if not chunk_ids:
        return {}
    statement = select(Chunk.id, Chunk.chunk_index, Chunk.starts_mid_sentence).where(
        Chunk.tenant_id == context.tenant_id,
        Chunk.id.in_(chunk_ids),
    )
    rows = (await session.execute(statement)).all()
    return {
        row.id: ChunkPosition(index=row.chunk_index, starts_mid_sentence=row.starts_mid_sentence)
        for row in rows
    }


def _counter(model: str) -> Callable[[str], int]:
    """The same token counter the gateway prices with, bound to the task's model."""
    return lambda text: count_tokens(text, model)


def _title_from(question: str) -> str:
    collapsed = " ".join(question.split())
    if len(collapsed) <= _TITLE_CHARS:
        return collapsed or "New conversation"
    return collapsed[: _TITLE_CHARS - 1].rstrip() + "..."


def unique(values: Sequence[str]) -> list[str]:
    """De-duplicate degradation reasons while preserving their order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _utcnow() -> datetime:
    """Timezone-aware now, so message ordering never depends on the server clock."""
    return datetime.now(UTC)


__all__ = [
    "ChatAnswer",
    "PreparedTurn",
    "ask",
    "build_generator",
    "persist_assistant_message",
    "prepare_turn",
    "unique",
    "validate_question",
]
