from pydantic_ai import Agent
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
import logging
from schemas.evaluation import MisconceptionType, MisconceptionSchema

logger = logging.getLogger(__name__)

class DetailedMisconception(BaseModel):
    type: MisconceptionType
    description: str
    corrected_statement: str
    severity: str
    concept_gap: str  # What concept they're missing
    remediation_suggestion: str

class MisconceptionReport(BaseModel):
    misconceptions: List[DetailedMisconception]
    overall_assessment: str
    priority_topics: List[str]

misconception_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=MisconceptionReport,
    retries=3
)

class MisconceptionDetector:
    """Specialized agent for detecting and categorizing misconceptions"""
    
    async def detect(
        self,
        student_answer: str,
        correct_context: str,
        question: str
    ) -> MisconceptionReport:
        """Deep analysis of student misconceptions"""
        prompt = f"""
        Analyze this student's answer for deep misconceptions.
        
        QUESTION: {question}
        
        STUDENT ANSWER: {student_answer}
        
        CORRECT CONTEXT: {correct_context}
        
        For each misconception, identify:
        1. The specific error or misunderstanding
        2. Why it's wrong (conceptually)
        3. What concept they're missing
        4. How to fix it
        
        Categorize by:
        - factual_error: Wrong facts
        - incomplete: Missing key parts
        - confused_concept: Mixing up related ideas
        - missing_context: No background/examples
        - logical_fallacy: Bad reasoning
        - terminology_error: Wrong terms/definitions
        
        Return structured analysis with remediation suggestions.
        """
        
        result = await misconception_agent.run(prompt)
        return result.output
    
    def generate_remediation_plan(
        self,
        misconceptions: List[DetailedMisconception],
        available_notes: List[dict]
    ) -> dict:
        """Generate a study plan based on detected misconceptions"""
        remediation = {
            "immediate_actions": [],
            "study_resources": [],
            "practice_exercises": [],
            "estimated_time_hours": 0
        }
        
        for m in misconceptions:
            if m.severity == "severe":
                remediation["immediate_actions"].append({
                    "misconception": m.description,
                    "action": m.remediation_suggestion,
                    "priority": "high"
                })
                remediation["estimated_time_hours"] += 2
            elif m.severity == "moderate":
                remediation["immediate_actions"].append({
                    "misconception": m.description,
                    "action": m.remediation_suggestion,
                    "priority": "medium"
                })
                remediation["estimated_time_hours"] += 1
            else:
                remediation["immediate_actions"].append({
                    "misconception": m.description,
                    "action": m.remediation_suggestion,
                    "priority": "low"
                })
                remediation["estimated_time_hours"] += 0.5
        
        for m in misconceptions:
            for note in available_notes:
                if any(keyword in note.get("topic", "").lower() for keyword in m.concept_gap.lower().split()[:3]):
                    remediation["study_resources"].append({
                        "topic": note.get("topic"),
                        "note_id": note.get("id"),
                        "relevant_to": m.description
                    })
        
        return remediation
