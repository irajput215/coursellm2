from pydantic_ai import Agent
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Tuple
import logging
from sqlmodel import Session, select

from models.knowledge import GeneratedNote
from rag.retrieval.hybrid_search import HybridRetriever
from schemas.evaluation import EvaluationResult, MisconceptionSchema, MisconceptionType

logger = logging.getLogger(__name__)


class RubricScores(BaseModel):
    factual_accuracy: float = Field(ge=0, le=10, description="Correctness vs source material")
    relevance: float = Field(ge=0, le=10, description="Addresses the question directly")
    coherence: float = Field(ge=0, le=10, description="Logical flow and clarity")
    completeness: float = Field(ge=0, le=10, description="Covers all key concepts")


class DetectedMisconception(BaseModel):
    type: MisconceptionType
    description: str
    corrected_statement: str
    severity: str


class StructuredEvaluation(BaseModel):
    rubric_scores: RubricScores
    misconceptions: List[DetectedMisconception]
    feedback: str
    suggested_topics: List[str]


evaluation_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=StructuredEvaluation,
    retries=3,
)


def retrieve_relevant_context_for_course(
    session: Session,
    question: str,
    user_id: int,
    course_id: int,
    document_ids: list[int],
    limit: int = 5,
) -> Tuple[str, Optional[int]]:
    """Retrieve notes and chunks across all documents in a course."""
    context_parts: list[str] = []
    seen_note_topics: set[str] = set()
    primary_document_id: Optional[int] = None

    keywords = [word for word in question.lower().split()[:10] if len(word) >= 3]
    for keyword in keywords:
        notes = session.exec(
            select(GeneratedNote).where(
                GeneratedNote.document_id.in_(document_ids),
                GeneratedNote.topic.ilike(f"%{keyword}%"),
            ).limit(limit)
        ).all()
        for note in notes:
            if note.topic in seen_note_topics:
                continue
            seen_note_topics.add(note.topic)
            context_parts.append(
                f"[Topic: {note.topic}]\nSummary: {note.summary}\n"
                f"Key Points: {', '.join(note.key_points)}"
            )
            if primary_document_id is None:
                primary_document_id = note.document_id

    retriever = HybridRetriever(session)
    search_results = retriever.search(
        question=question,
        user_id=user_id,
        course_id=course_id,
        top_k=limit,
    )
    for result in search_results:
        chunk = result.chunk
        context_parts.append(
            f"[Document {chunk.document_id}, Page {chunk.page}]\n{chunk.content}"
        )
        if primary_document_id is None:
            primary_document_id = chunk.document_id

    if not context_parts:
        return "No course material found for this question.", primary_document_id

    return "\n\n---\n\n".join(context_parts[:8]), primary_document_id


async def evaluate_answer(
    question: str,
    student_answer: str,
    context: str,
    expected_answer: Optional[str] = None,
    custom_rubric: Optional[Dict] = None,
) -> EvaluationResult:
    """Evaluate a student answer against provided course context."""

    rubric_instruction = ""
    if custom_rubric:
        rubric_instruction = f"""
        CUSTOM RUBRIC:
        {custom_rubric}
        """
    else:
        rubric_instruction = """
        DEFAULT RUBRIC (score 0-10 for each):
        - factual_accuracy: Correctness compared to course material
        - relevance: How well answer addresses the question
        - coherence: Logical structure and clarity
        - completeness: Coverage of key concepts
        """

    expected_instruction = ""
    if expected_answer:
        expected_instruction = f"""
        EXPECTED ANSWER (for reference):
        {expected_answer}
        """

    prompt = f"""
    You are an expert educator evaluating a student's answer.

    QUESTION:
    {question}

    STUDENT'S ANSWER:
    {student_answer}

    COURSE MATERIAL (reference):
    {context}

    {expected_instruction}

    {rubric_instruction}

    INSTRUCTIONS:

    1. Score each rubric criteria (0-10)

    2. Identify SPECIFIC misconceptions in the student's answer:
       - factual_error: Wrong facts or incorrect statements
       - incomplete: Missing key information
       - confused_concept: Mixing up related concepts
       - missing_context: Not providing necessary background
       - logical_fallacy: Flawed reasoning
       - terminology_error: Wrong terminology or definitions

    3. For each misconception, provide:
       - What the student got wrong
       - The corrected understanding
       - Severity (minor/moderate/severe)

    4. Write constructive feedback that:
       - Highlights what they did well
       - Explains misconceptions clearly
       - Suggests how to improve

    5. Suggest 3-5 specific topics to review based on misconceptions

    Be thorough but encouraging. Focus on learning, not just scoring.
    """

    try:
        result = await evaluation_agent.run(prompt)
        eval_data = result.output

        scores = eval_data.rubric_scores
        total_score = (
            scores.factual_accuracy * 3.5
            + scores.relevance * 2.5
            + scores.coherence * 2.0
            + scores.completeness * 2.0
        )

        misconceptions = [
            MisconceptionSchema(
                type=m.type,
                description=m.description,
                corrected_statement=m.corrected_statement,
                severity=m.severity,
            )
            for m in eval_data.misconceptions
        ]

        return EvaluationResult(
            total_score=round(total_score, 2),
            rubric_scores={
                "factual_accuracy": scores.factual_accuracy,
                "relevance": scores.relevance,
                "coherence": scores.coherence,
                "completeness": scores.completeness,
            },
            misconceptions=misconceptions,
            feedback=eval_data.feedback,
            suggested_topics=eval_data.suggested_topics[:5],
        )

    except Exception as exc:
        logger.error("Evaluation failed: %s", exc)
        raise
