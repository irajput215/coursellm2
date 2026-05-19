from sqlmodel import create_engine, Session
from core.config import settings
from typing import Generator

db_url = settings.DATABASE_URL
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://")

connect_args = {}
if "postgresql://" in db_url:
    connect_args["connect_timeout"] = 5

engine = create_engine(
    db_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session