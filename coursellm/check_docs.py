from sqlmodel import Session, select
from database import engine
from models.document import Document

with Session(engine) as session:
    docs = session.exec(select(Document)).all()
    for d in docs:
        print(f"ID: {d.id}, File: {d.filename}, UserID: {d.user_id}")
