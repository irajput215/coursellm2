from sqlmodel import Session, select
from database import engine
from models.user import User

with Session(engine) as session:
    users = session.exec(select(User)).all()
    for u in users:
        print(f"ID: {u.id}, Username: {u.username}")
