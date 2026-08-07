from sqlmodel import Session, select
from database import engine
from models.chunk import Chunk
from models.document import Document
from models.course import Course

with Session(engine) as session:
    chunks = session.exec(select(Chunk)).all()
    docs = session.exec(select(Document)).all()
    courses = session.exec(select(Course)).all()
    print(f"Courses: {len(courses)}")
    print(f"Documents: {len(docs)}")
    print(f"Chunks: {len(chunks)}")
    for d in docs:
        c_count = len(session.exec(select(Chunk).where(Chunk.document_id == d.id)).all())
        print(f"Doc {d.filename}: {c_count} chunks")
