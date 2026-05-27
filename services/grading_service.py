from sqlmodel import Session, select, text
from datetime import datetime
import logging

from agents.evaluation_agent import evaluate_answer, retrieve_relevant_context_for_course
from agents.misconception_detector import MisconceptionDetector
from agents.feedback_generator import FeedbackGenerator
from models.evaluation import Submission, Evaluation, Misconception
from schemas.evaluation import EvaluationRequest, EvaluationResponse
from services.evaluation_context import EvaluationContext

logger = logging.getLogger(__name__)


class GradingService:
    """Orchestrates the complete evaluation pipeline."""

    def __init__(self):
        self.misconception_detector = MisconceptionDetector()
        self.feedback_generator = FeedbackGenerator()

    async def grade_submission(
        self,
        request: EvaluationRequest,
        context: EvaluationContext,
        session: Session,
    ) -> EvaluationResponse:
        course_context, primary_document_id = retrieve_relevant_context_for_course(
            session=session,
            question=request.question,
            user_id=context.user_id,
            course_id=context.course_id,
            document_ids=context.document_ids,
        )

        evaluation = await evaluate_answer(
            question=request.question,
            student_answer=request.student_answer,
            context=course_context,
            expected_answer=request.expected_answer,
            custom_rubric=request.custom_rubric.model_dump()
            if request.custom_rubric
            else None,
        )

        if evaluation.misconceptions:
            await self.misconception_detector.detect(
                student_answer=request.student_answer,
                correct_context=course_context,
                question=request.question,
            )

        detailed_feedback = await self.feedback_generator.generate(
            student_answer=request.student_answer,
            evaluation_result=evaluation.model_dump(),
            misconceptions=[m.model_dump() for m in evaluation.misconceptions],
        )

        combined_feedback = f"""
        {detailed_feedback.summary}

        STRENGTHS:
        {chr(10).join(f'• {s}' for s in detailed_feedback.strengths)}

        AREAS TO IMPROVE:
        {chr(10).join(f'• {a}' for a in detailed_feedback.areas_for_improvement)}

        ACTIONABLE SUGGESTIONS:
        {chr(10).join(f'• {s}' for s in detailed_feedback.actionable_suggestions)}

        {detailed_feedback.encouraging_note}
        """

        submission = Submission(
            course_id=context.course_id,
            user_id=context.user_id,
            document_id=primary_document_id,
            question=request.question,
            student_answer=request.student_answer,
            expected_answer=request.expected_answer,
            evaluated=True,
        )
        session.add(submission)
        session.flush()

        evaluation_record = Evaluation(
            submission_id=submission.id,
            total_score=evaluation.total_score,
            rubric_scores=evaluation.rubric_scores,
            feedback=combined_feedback,
            suggested_topics=evaluation.suggested_topics,
        )
        session.add(evaluation_record)
        session.flush()

        for misconception in evaluation.misconceptions:
            session.add(
                Misconception(
                    evaluation_id=evaluation_record.id,
                    misconception_type=misconception.type,
                    description=misconception.description,
                    corrected_statement=misconception.corrected_statement,
                    severity=misconception.severity,
                )
            )

        session.commit()
        session.refresh(submission)

        return EvaluationResponse(
            submission_id=submission.id,
            course_name=context.course_name,
            document_ids_used=context.document_ids,
            evaluation=evaluation,
            created_at=datetime.utcnow(),
        )

    async def get_submission_history(
        self,
        context: EvaluationContext,
        session: Session,
        limit: int = 10,
    ) -> list[dict]:
        submissions = session.exec(
            select(Submission)
            .where(
                Submission.course_id == context.course_id,
                Submission.user_id == context.user_id,
            )
            .order_by(Submission.submitted_at.desc())
            .limit(limit)
        ).all()

        results = []
        for submission in submissions:
            if submission.evaluation:
                results.append(
                    {
                        "id": submission.id,
                        "question": submission.question,
                        "student_answer": submission.student_answer[:200],
                        "score": submission.evaluation.total_score,
                        "submitted_at": submission.submitted_at,
                    }
                )
        return results

    async def get_student_progress(
        self,
        context: EvaluationContext,
        session: Session,
    ) -> dict:
        evaluations = session.exec(
            select(Evaluation)
            .join(Submission)
            .where(
                Submission.course_id == context.course_id,
                Submission.user_id == context.user_id,
            )
        ).all()

        if not evaluations:
            return {"message": "No submissions yet", "course_name": context.course_name}

        scores = [evaluation.total_score for evaluation in evaluations]
        avg_score = sum(scores) / len(scores)
        trending = (
            "improving" if len(scores) >= 2 and scores[-1] > scores[0] else "needs work"
        )

        misconceptions = session.exec(
            text(
                """
                SELECT m.misconception_type, COUNT(*) as count
                FROM misconceptions m
                JOIN evaluations e ON m.evaluation_id = e.id
                JOIN submissions s ON e.submission_id = s.id
                WHERE s.course_id = :course_id AND s.user_id = :user_id
                GROUP BY m.misconception_type
                ORDER BY count DESC
                LIMIT 3
                """
            ),
            {"course_id": context.course_id, "user_id": context.user_id},
        ).all()

        return {
            "course_name": context.course_name,
            "submission_count": len(evaluations),
            "average_score": round(avg_score, 2),
            "highest_score": max(scores),
            "lowest_score": min(scores),
            "trend": trending,
            "common_misconceptions": [
                {"type": row[0], "count": row[1]} for row in misconceptions
            ],
            "recommended_topics": self._get_recommended_topics(misconceptions),
        }

    def _get_recommended_topics(self, misconceptions: list) -> list[str]:
        recommendations = []
        for m_type, _count in misconceptions:
            if m_type == "factual_error":
                recommendations.append("Review basic concepts and definitions")
            elif m_type == "incomplete":
                recommendations.append("Practice writing more comprehensive answers")
            elif m_type == "confused_concept":
                recommendations.append("Compare and contrast related concepts")
            elif m_type == "terminology_error":
                recommendations.append("Review glossary and key terms")
        return recommendations
