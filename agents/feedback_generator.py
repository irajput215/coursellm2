from pydantic_ai import Agent
from pydantic import BaseModel
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

class FeedbackStyle(BaseModel):
    encouraging: str
    constructive: str
    specific_improvements: List[str]
    next_steps: List[str]

class DetailedFeedback(BaseModel):
    summary: str
    strengths: List[str]
    areas_for_improvement: List[str]
    actionable_suggestions: List[str]
    encouraging_note: str

feedback_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=DetailedFeedback,
    retries=3
)

class FeedbackGenerator:
    """Generates personalized, actionable feedback"""
    
    async def generate(
        self,
        student_answer: str,
        evaluation_result: dict,
        misconceptions: List[dict],
        student_history: Optional[List[dict]] = None
    ) -> DetailedFeedback:
        """Generate personalized feedback based on evaluation"""
        history_context = ""
        if student_history and len(student_history) > 0:
            history_context = f"""
            PREVIOUS SUBMISSIONS:
            Average score: {sum(h.get('score', 0) for h in student_history) / len(student_history):.1f}
            Common issues: {self._extract_common_issues(student_history)}
            """
        
        prompt = f"""
        Generate personalized, actionable feedback for this student.
        
        STUDENT ANSWER: {student_answer}
        
        EVALUATION:
        - Score: {evaluation_result.get('total_score', 0)}/100
        - Accuracy: {evaluation_result.get('rubric_scores', {}).get('factual_accuracy', 0)}/10
        - Completeness: {evaluation_result.get('rubric_scores', {}).get('completeness', 0)}/10
        
        MISCONCEPTIONS:
        {misconceptions}
        
        {history_context}
        
        FEEDBACK REQUIREMENTS:
        
        1. Start with something encouraging (what they did well)
        2. Be specific about errors, not generic
        3. Provide actionable steps to improve
        4. Reference specific concepts they need to review
        5. End with motivation to continue learning
        
        Tone: Supportive, constructive, educational
        Length: 3-5 paragraphs
        """
        
        result = await feedback_agent.run(prompt)
        return result.output
    
    def _extract_common_issues(self, history: List[dict]) -> str:
        """Extract recurring issues from student history"""
        issues = []
        for submission in history:
            misconceptions = submission.get('misconceptions', [])
            for m in misconceptions:
                if isinstance(m, dict):
                    issues.append(m.get('description', ''))
        
        from collections import Counter
        common = Counter(issues).most_common(3)
        return ", ".join([f"{issue[0]} ({issue[1]}x)" for issue in common])
