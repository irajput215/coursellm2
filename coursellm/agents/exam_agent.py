from pydantic_ai import Agent
from pydantic import BaseModel, Field
from typing import List
import logging
import random
from sqlmodel import Session, select

from models.document import Document
from models.knowledge import GeneratedNote

logger = logging.getLogger(__name__)

class ExamQuestions(BaseModel):
    questions: List[str] = Field(description="List of 5 important exam questions")

exam_question_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=ExamQuestions,
    retries=3,
)

async def generate_exam_questions_for_course(
    session: Session,
    course_id: int,
    user_id: int,
) -> List[str]:
    # 1. Get all documents for course and user
    docs = session.exec(
        select(Document.id)
        .where(Document.course_id == course_id, Document.user_id == user_id)
    ).all()

    if not docs:
        return ["No documents found for this course to generate questions."]

    # 2. Get GeneratedNotes for these documents
    notes = session.exec(
        select(GeneratedNote)
        .where(GeneratedNote.document_id.in_(docs))
    ).all()

    if not notes:
        return ["Not enough processed knowledge found. Please ensure documents have finished processing."]

    # Shuffle and pick a subset to fit context limit
    random.shuffle(notes)
    selected_notes = notes[:15]

    context_parts = []
    for note in selected_notes:
        context_parts.append(
            f"[Topic: {note.topic}]\nSummary: {note.summary}\nKey Points: {', '.join(note.key_points)}"
        )

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""
    You are an expert university professor creating practice exam questions.
    Based on the following extracted course notes, generate exactly 5 high-quality, thought-provoking exam questions.
    The questions should test deep understanding of the concepts rather than simple memorization.

    COURSE MATERIAL:
    {context}

    INSTRUCTIONS:
    1. Output exactly 5 questions.
    2. Make the questions challenging but entirely answerable using ONLY the provided course material.
    3. Do not include the answers, just the questions.
    """

    try:
        result = await exam_question_agent.run(prompt)
        return result.output.questions
    except Exception as exc:
        logger.error(f"Failed to generate exam questions: {exc}")
        return ["Failed to generate exam questions. Please try again later."]
