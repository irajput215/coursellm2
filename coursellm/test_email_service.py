import unittest
from datetime import datetime

from core import config
from mcp.email_service import Email, EmailService


class EmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.email_service = EmailService()
        self._original_llm = config.settings.EMAIL_USE_LLM_EXTRACTION
        config.settings.EMAIL_USE_LLM_EXTRACTION = False

    def tearDown(self):
        config.settings.EMAIL_USE_LLM_EXTRACTION = self._original_llm

    async def test_fetch_filters_by_course_code(self):
        emails = await self.email_service.fetch_recent_emails("COMP9044")
        self.assertEqual(len(emails), 2)
        for email in emails:
            combined = f"{email.subject} {email.body}".upper()
            self.assertIn("COMP9044", combined)

    async def test_fetch_skips_unrelated_course(self):
        emails = await self.email_service.fetch_recent_emails("COMP9331")
        self.assertEqual(len(emails), 1)
        self.assertIn("COMP9331", emails[0].subject)

    async def test_fetch_skips_non_actionable_course_email(self):
        emails = await self.email_service.fetch_recent_emails("COMP9999")
        self.assertEqual(emails, [])

    async def test_extract_assignment_from_body_when_subject_generic(self):
        email = Email(
            id="exam_email",
            from_address="exams@unsw.edu.au",
            subject="Final Exam Schedule Released",
            body="COMP9044 Final Exam: June 15th, 9:00 AM. Venue: Online.",
            received_at=datetime.utcnow(),
            is_course_email=True,
        )
        events = await self.email_service.extract_events_from_email(
            email, course_code="COMP9044"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "exam")
        self.assertEqual(events[0].priority, "high")
        self.assertEqual(events[0].extraction_method, "rules")

    async def test_extract_assignment_event(self):
        email = Email(
            id="assignment_email",
            from_address="course@unsw.edu.au",
            subject="COMP9044: Assignment 1 Due Next Week",
            body="Assignment 1 is due on June 20th at 5pm.",
            received_at=datetime.utcnow(),
            is_course_email=True,
        )
        events = await self.email_service.extract_events_from_email(
            email, course_code="COMP9044"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "assignment")
        self.assertIsNotNone(events[0].due_date)
        self.assertEqual(events[0].due_date.month, 6)
        self.assertEqual(events[0].due_date.day, 20)
        self.assertEqual(events[0].due_date.hour, 17)

    async def test_extract_exam_date_not_course_code(self):
        email = Email(
            id="exam_email",
            from_address="exams@unsw.edu.au",
            subject="Final Exam Schedule Released",
            body="COMP9044 Final Exam: June 15th, 9:00 AM. Venue: Online.",
            received_at=datetime.utcnow(),
            is_course_email=True,
        )
        events = await self.email_service.extract_events_from_email(
            email, course_code="COMP9044"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].due_date.month, 6)
        self.assertEqual(events[0].due_date.day, 15)
        self.assertEqual(events[0].due_date.hour, 9)


class CalendarServiceDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_exists_returns_false_for_unknown(self):
        from mcp.calendar_service import CalendarService
        from models.planner import EventType

        calendar = CalendarService()
        exists = await calendar.event_exists(
            user_id="999999",
            source_email_id="does-not-exist",
            event_type=EventType.ASSIGNMENT,
            document_id=1,
        )
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
