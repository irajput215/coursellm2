from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from api.routes.auth import CurrentUserDep, SessionDep
from models.course import Course
from rag.retrieval.hybrid_search import HybridRetriever
from rag.reranking.reranker import Reranker
from rag.generation.generator import AnswerGenerator

router = APIRouter()

class AskRequest(BaseModel):
    question: str
    course_name: str | None = None

class AskResponse(BaseModel):
    answer: str
    sources: list[str]

@router.post("/", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Semantic Retrieval and Generation endpoint.
    Retrieves the most relevant chunks using vector search and generates an answer using Groq.
    """
    try:
        # Resolve course name to ID if provided
        course_id = None
        if request.course_name:
            course = session.exec(
                select(Course).where(Course.name == request.course_name, Course.user_id == current_user.id)
            ).first()
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")
            course_id = course.id

        # 1. Hybrid Retrieval (Broad Recall)
        retriever = HybridRetriever(session)
        search_results = retriever.search(
            question=request.question,
            user_id=current_user.id,
            course_id=course_id,
            top_k=20
        )
        
        # 2. Reranking (Precision)
        reranker = Reranker.get_instance()
        reranked_results = reranker.rerank(request.question, search_results, top_k=5)
        
        # Extract chunks for generation
        context_chunks = [r.chunk for r in reranked_results]
        
        # 3. Generation
        generator = AnswerGenerator()
        answer = await generator.generate(request.question, context_chunks)
        
        # 3. Format sources
        sources = []
        for chunk in context_chunks:
            source_str = f"Document ID {chunk.document_id}, Page {chunk.page}"
            if source_str not in sources: 
                sources.append(source_str)
                
        return AskResponse(answer=answer, sources=sources)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process question: {str(e)}")
