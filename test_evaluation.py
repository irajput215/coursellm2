import asyncio
import sys
from pathlib import Path
import os

from core.config import settings
from sqlmodel import Session

os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY
sys.path.append(str(Path(__file__).parent.parent))

from database import engine
from models.user import User
from services.grading_service import GradingService
from services.evaluation_context import resolve_evaluation_context
from schemas.evaluation import EvaluationRequest


async def test_evaluation():
    service = GradingService()

    with Session(engine) as session:
        user = session.get(User, 1)
        if not user:
            print("User id=1 not found; register a user first.")
            return

        context = resolve_evaluation_context(session, user, "COMP9044")
        request = EvaluationRequest(
            question="What is a Unix filter? Give an example.",
            student_answer=(
                "A Unix filter is a program that transforms data. "
                "For example, grep searches for patterns."
            ),
            course_name="COMP9044",
            expected_answer=(
                "A Unix filter is a program that reads from standard input, "
                "transforms the byte stream, and writes to standard output. "
                "Examples include grep, sed, awk, wc, and tr."
            ),
        )

        print(f"Evaluating against course {context.course_name} "
              f"({len(context.document_ids)} documents)...")
        result = await service.grade_submission(request, context, session)

    print(f"\nScore: {result.evaluation.total_score}/100")
    print(f"Documents used: {result.document_ids_used}")
    print(f"Rubric: {result.evaluation.rubric_scores}")
    print(f"Misconceptions: {len(result.evaluation.misconceptions)}")
    for misconception in result.evaluation.misconceptions:
        print(
            f"  - [{misconception.type}] {misconception.description} -> "
            f"{misconception.corrected_statement}"
        )
    print(f"\nFeedback:\n{result.evaluation.feedback}")
    print(f"\nSuggested Topics: {result.evaluation.suggested_topics}")


if __name__ == "__main__":
    asyncio.run(test_evaluation())
