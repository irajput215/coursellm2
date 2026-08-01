from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from api.routes.auth import CurrentUserDep, SessionDep
from core.config import settings
from models.course import Course
from observability.context import get_request_id, set_course_id, set_user_id
from observability.event_store import get_events
from observability.tracing.langfuse_client import flush_traces, trace_span
from rag.retrieval.hybrid_search import HybridRetriever
from rag.reranking.reranker import Reranker
from rag.generation.generator import AnswerGenerator

router = APIRouter()

class AskRequest(BaseModel):
    question: str
    course_name: str | None = None
    debug: bool = False

class AskResponse(BaseModel):
    answer: str
    sources: list[str]
    debug_events: list[dict[str, Any]] | None = None

@router.post("/", response_model=AskResponse, response_model_exclude_none=True)
async def ask_question(
    request: AskRequest,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Semantic Retrieval and Generation endpoint.
    Retrieves the most relevant chunks using vector search and generates an answer using Groq.

    Set `"debug": true` to include pipeline events in the response (dev only).
    """
    if request.debug and not settings.OBSERVABILITY_DEBUG_API_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Debug mode is disabled (OBSERVABILITY_DEBUG_API_ENABLED=false)",
        )

    set_user_id(current_user.id)
    try:
        with trace_span(
            "ask",
            input={
                "question": request.question,
                "course_name": request.course_name,
            },
            metadata={"route": "POST /ask"},
        ):
            course_id = None
            with trace_span("course_resolve", input={"course_name": request.course_name}) as span:
                if request.course_name:
                    course = session.exec(
                        select(Course).where(
                            Course.name == request.course_name,
                            Course.user_id == current_user.id,
                        )
                    ).first()
                    if not course:
                        raise HTTPException(status_code=404, detail="Course not found")
                    course_id = course.id
                    set_course_id(course_id)
                    span.update(output={"course_id": course_id, "course_name": course.name})

            retriever = HybridRetriever(session)
            search_results = retriever.search(
                question=request.question,
                user_id=current_user.id,
                course_id=course_id,
                top_k=20,
            )
            
            reranker = Reranker.get_instance()
            reranked_results = reranker.rerank(request.question, search_results, top_k=5)
            
            context_chunks = [r.chunk for r in reranked_results]
            
            generator = AnswerGenerator()
            answer = await generator.generate(request.question, context_chunks)
            
            sources = []
            for chunk in context_chunks:
                source_str = f"Document ID {chunk.document_id}, Page {chunk.page}"
                if source_str not in sources: 
                    sources.append(source_str)

            response = AskResponse(answer=answer, sources=sources)
            if request.debug:
                request_id = get_request_id()
                response.debug_events = get_events(request_id) if request_id else []

            return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process question: {str(e)}")
    finally:
        flush_traces()
