import asyncio
import sys
from pathlib import Path
import os

from core.config import settings

os.environ["GROQ_API_KEY"] = settings.GROQ_API_KEY
sys.path.append(str(Path(__file__).parent.parent))

from agents.planner_agent import PlannerAgent
from mcp.calendar_service import CalendarService


async def test_planner():
    agent = PlannerAgent()
    user_id = "test_user_planner"
    course_name = "COMP9044"
    document_id = 4

    print("Testing process_incoming_emails...")
    email_result = await agent.process_incoming_emails(
        user_id=user_id,
        course_name=course_name,
        document_id=document_id,
    )
    print(email_result)

    print("\nTesting duplicate skip on second run...")
    duplicate_result = await agent.process_incoming_emails(
        user_id=user_id,
        course_name=course_name,
        document_id=document_id,
    )
    print(duplicate_result)

    print("\nTesting calendar events scoped to document...")
    calendar = CalendarService()
    events = await calendar.get_upcoming_events(
        user_id,
        days_ahead=30,
        document_id=document_id,
    )
    print(f"Found {len(events)} upcoming events for document {document_id}")

    print("\nSkipping generate_study_plan LLM call in script run.")
    print("Use POST /planner/generate-study-plan with auth for full flow.")


if __name__ == "__main__":
    asyncio.run(test_planner())
