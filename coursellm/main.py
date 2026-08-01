from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel

from api.routes import auth, upload, ask, evaluation, planner, observability, courses
from database import engine
from observability.middleware import RequestContextMiddleware

# Import all models so SQLModel knows about them
import models.user
import models.course
import models.document
import models.chunk
import models.chat
import models.generation
import models.knowledge
import models.evaluation
import models.planner

# Create tables (For dev, use Alembic in production)
SQLModel.metadata.create_all(engine)

app = FastAPI(title ="Coursellm API")

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(ask.router, prefix="/ask", tags=["rag"])
app.include_router(evaluation.router)
app.include_router(planner.router)
app.include_router(observability.router)
app.include_router(courses.router, prefix="/courses")

@app.get("/")
def read_root():
    return {"message": "Welcome to the World Best Coursellm API!"}
