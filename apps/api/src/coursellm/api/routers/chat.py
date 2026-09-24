"""Chat endpoints: grounded answers, SSE streaming and conversation history.

Transport only. Retrieval, assembly, generation and persistence live in
:mod:`coursellm.services.chat`, so the same use case is reachable without HTTP.

The streaming endpoint emits named SSE events in a fixed order — ``token`` ...
``citations`` then ``done`` — with an ``error`` event on failure. A client can
therefore render partial text immediately and only treat the ``citations`` event
as authoritative.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from coursellm.api.deps import (
    ContextDep,
    LLMGatewayDep,
    SettingsDep,
    TenantSessionDep,
)
from coursellm.api.schemas.chat import (
    ChatRequest,
    ChatResponse,
    CitationResponse,
    ConversationDetailResponse,
    ConversationSummary,
    MessageResponse,
    UsageSummary,
)
from coursellm.core.errors import NotFoundError
from coursellm.core.logging import get_logger
from coursellm.db.models.conversation import Conversation, Message
from coursellm.db.tenancy import TenantContext
from coursellm.rag.generation.generator import GeneratedAnswer, TokenEvent
from coursellm.services import chat as chat_service

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_ERROR_DETAIL = (
    "The answer stream failed before it completed. Quote the request id when reporting it."
)


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ask a grounded question",
    description=(
        "Retrieves evidence from the caller's own course material, answers with "
        "verified inline citations, and refuses explicitly when the evidence does "
        "not support an answer. The turn is persisted either way. ``engine`` "
        "selects the bounded agent graph (the default) or the direct "
        "retrieval-augmented path; both share the grounding, citation and refusal "
        "machinery and produce equivalent answers for a simple factual question."
    ),
)
async def chat(
    payload: ChatRequest,
    context: ContextDep,
    settings: SettingsDep,
    session: TenantSessionDep,
    gateway: LLMGatewayDep,
) -> ChatResponse:
    result = await chat_service.ask(
        session,
        settings,
        gateway,
        context,
        question=payload.question,
        course_id=payload.course_id,
        conversation_id=payload.conversation_id,
        engine=payload.engine,
    )
    return ChatResponse(
        answer=result.answer,
        citations=[CitationResponse.model_validate(citation) for citation in result.citations],
        grounded=result.grounded,
        degraded=result.degraded,
        conversation_id=result.conversation_id,
        usage=UsageSummary.model_validate(result.usage) if result.usage is not None else None,
        intent=result.intent,
        trace_id=result.trace_id,
    )


@router.post(
    "/stream",
    summary="Ask a grounded question and stream the answer",
    response_class=EventSourceResponse,
    description=(
        "Server-sent events. ``token`` events carry partial text as it is "
        "produced; the final ``citations`` event carries the verified citation "
        "list; ``done`` ends the stream. An ``error`` event is emitted instead if "
        "the stream fails. With ``engine='agent'`` (the default) the graph runs "
        "the turn first and its answer is emitted as a single ``token`` event."
    ),
)
async def stream_chat(
    payload: ChatRequest,
    context: ContextDep,
    settings: SettingsDep,
    session: TenantSessionDep,
    gateway: LLMGatewayDep,
) -> EventSourceResponse:
    if payload.engine == "agent":
        # The graph composes the whole answer (and persists the turn) before the
        # response starts, so the SSE contract is preserved by emitting that
        # answer as one token event followed by the authoritative citations.
        result = await chat_service.ask(
            session,
            settings,
            gateway,
            context,
            question=payload.question,
            course_id=payload.course_id,
            conversation_id=payload.conversation_id,
            engine="agent",
        )
        return EventSourceResponse(_agent_events(result))

    prepared = await chat_service.prepare_turn(
        session,
        settings,
        context,
        question=payload.question,
        course_id=payload.course_id,
        conversation_id=payload.conversation_id,
    )
    generator = chat_service.build_generator(settings, gateway)

    async def events() -> AsyncIterator[dict[str, str]]:
        completion: GeneratedAnswer | None = None
        try:
            async for event in generator.stream(
                prepared.question,
                prepared.assembled,
                course_name=prepared.course_name,
                history=prepared.history,
            ):
                if isinstance(event, TokenEvent):
                    yield {"event": "token", "data": _encode({"text": event.text})}
                else:
                    completion = event.answer
            if completion is None:
                raise RuntimeError("The generator stream ended without a completion event.")
            degraded = chat_service.unique([*prepared.degraded, *completion.degraded])
            await chat_service.persist_assistant_message(
                session,
                settings,
                context,
                prepared=prepared,
                generated=completion,
                degraded=degraded,
            )
            yield {
                "event": "citations",
                "data": _encode(
                    [citation.model_dump(mode="json") for citation in completion.citations]
                ),
            }
            yield {"event": "done", "data": "{}"}
        except Exception:
            # The response has already started, so the error cannot become a JSON
            # status code; it becomes an SSE event the client can surface instead.
            logger.exception("chat_stream_failed")
            yield {"event": "error", "data": _encode({"detail": _ERROR_DETAIL})}

    return EventSourceResponse(events())


async def _agent_events(result: chat_service.ChatAnswer) -> AsyncIterator[dict[str, str]]:
    """Emit the graph's completed answer as the documented SSE sequence."""
    try:
        yield {"event": "token", "data": _encode({"text": result.answer})}
        yield {
            "event": "citations",
            "data": _encode([citation.model_dump(mode="json") for citation in result.citations]),
        }
        yield {"event": "done", "data": "{}"}
    except Exception:
        logger.exception("chat_stream_failed")
        yield {"event": "error", "data": _encode({"detail": _ERROR_DETAIL})}


@router.get(
    "/conversations",
    response_model=list[ConversationSummary],
    summary="List the caller's conversations",
)
async def list_conversations(
    context: ContextDep,
    session: TenantSessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ConversationSummary]:
    statement = (
        select(Conversation)
        .where(
            Conversation.tenant_id == context.tenant_id,
            Conversation.user_id == context.user_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(statement)).scalars().all()
    return [ConversationSummary.model_validate(row) for row in rows]


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="A conversation and its messages",
    description=(
        "A conversation belonging to another tenant or another user is reported "
        "as not found. Reporting it as forbidden would confirm that it exists."
    ),
)
async def get_conversation(
    conversation_id: uuid.UUID,
    context: ContextDep,
    session: TenantSessionDep,
) -> ConversationDetailResponse:
    conversation = await _owned_conversation(session, context, conversation_id)
    statement = (
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    messages = (await session.execute(statement)).scalars().all()
    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        course_id=conversation.course_id,
        created_at=conversation.created_at,
        messages=[MessageResponse.model_validate(message) for message in messages],
    )


async def _owned_conversation(
    session: AsyncSession, context: TenantContext, conversation_id: uuid.UUID
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == context.tenant_id,
        Conversation.user_id == context.user_id,
    )
    conversation = (await session.execute(statement)).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


def _encode(payload: Any) -> str:
    return json.dumps(payload)


__all__ = ["router"]
