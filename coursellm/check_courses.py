from sqlmodel import Session, select
from database import engine
from models.course import Course

with Session(engine) as session:
    courses = session.exec(select(Course)).all()
    for c in courses:
        print(f"ID: {c.id}, Name: {repr(c.name)}, UserID: {c.user_id}")
