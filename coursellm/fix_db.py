from sqlmodel import Session, select, text
from database import engine

with Session(engine) as session:
    session.exec(text("DELETE FROM documents WHERE user_id = 2"))
    session.commit()
    print("Deleted broken documents for user 2 via raw SQL.")
