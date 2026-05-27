from pydantic_ai import Agent
from pydantic import BaseModel
from typing import List, Dict
from datetime import datetime, timedelta
from sqlmodel import Session, select
from database import engine
from models.knowledge import DocumentTopic, GeneratedNote
from models.planner import StudyPlan, PlannerEvent
import logging

logger = logging.getLogger(__name__)

class WeeklyPlan(BaseModel):
    week_number: int
    topics: List[str]
    estimated_hours: float
    focus_areas: List[str]
    practice_recommendations: List[str]

class StudyPlanOutput(BaseModel):
    weekly_plans: List[WeeklyPlan]
    total_hours: float
    prerequisites: List[str]
    resources: List[str]

study_planner_agent = Agent(
    "groq:llama-3.3-70b-versatile",
    output_type=StudyPlanOutput,
    retries=3
)

class StudyPlanGenerator:
    """Generates personalized study plans"""
    
    async def generate_plan(
        self,
        user_id: str,
        document_id: int,
        upcoming_events: List[dict],
        weeks: int = 4
    ) -> StudyPlanOutput:
        """Generate a study plan based on course content and deadlines"""
        
        # Get course topics
        with Session(engine) as session:
            topics = session.exec(
                select(DocumentTopic)
                .where(DocumentTopic.document_id == document_id)
                .order_by(DocumentTopic.topic_order)
            ).all()
            
            topic_names = [t.topic_name for t in topics]
        
        # Get upcoming deadlines
        deadlines = []
        for event in upcoming_events:
            if event.get('due_date'):
                deadlines.append({
                    "title": event.get('title'),
                    "due_date": event.get('due_date'),
                    "type": event.get('event_type')
                })
        
        prompt = f"""
        Create a {weeks}-week study plan for a student.
        
        COURSE TOPICS (in order):
        {topic_names}
        
        UPCOMING DEADLINES:
        {deadlines}
        
        Requirements:
        - Distribute topics across {weeks} weeks logically
        - Prioritize topics needed for upcoming deadlines
        - Estimate realistic study hours per week (10-20 hours)
        - Include practice recommendations
        - Identify prerequisites within the topics
        
        Return structured weekly plan.
        """
        
        result = await study_planner_agent.run(prompt)
        plan_data = result.output
        
        # Save to database
        with Session(engine) as session:
            for week in plan_data.weekly_plans:
                study_plan = StudyPlan(
                    user_id=user_id,
                    document_id=document_id,
                    week_number=week.week_number,
                    topics=week.topics,
                    estimated_hours=week.estimated_hours,
                    is_active=(week.week_number == 1)  # First week is active
                )
                session.add(study_plan)
            session.commit()
        
        return plan_data
    
    async def generate_weekly_summary(
        self,
        user_id: str,
        week_number: int
    ) -> str:
        """Generate a weekly study summary with progress tracking"""
        
        with Session(engine) as session:
            plan = session.exec(
                select(StudyPlan)
                .where(
                    StudyPlan.user_id == user_id,
                    StudyPlan.week_number == week_number
                )
            ).first()
            
            if not plan:
                return "No study plan found for this week."
            
            # Get completed events for this week
            week_start = datetime.utcnow() - timedelta(days=7)
            events = session.exec(
                select(PlannerEvent)
                .where(
                    PlannerEvent.user_id == user_id,
                    PlannerEvent.completed == True,
                    PlannerEvent.updated_at >= week_start
                )
            ).all()
        
        prompt = f"""
        Generate a weekly study summary for Week {week_number}.
        
        PLANNED TOPICS:
        {plan.topics}
        
        ESTIMATED HOURS: {plan.estimated_hours}
        
        COMPLETED TASKS:
        {[e.title for e in events]}
        
        Create an encouraging summary that:
        1. Highlights completed work
        2. Identifies gaps
        3. Suggests focus for next week
        """
        
        summary_agent = Agent("groq:llama-3.3-70b-versatile", retries=3)
        result = await summary_agent.run(prompt)
        
        return result.output
