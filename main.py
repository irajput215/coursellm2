from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel

from api.routes import auth, upload, ask
from database import engine

# Import all models so SQLModel knows about them
import models.user
import models.course
import models.document
import models.chunk
import models.chat
import models.generation
import models.knowledge

# Create tables (For dev, use Alembic in production)
SQLModel.metadata.create_all(engine)

app = FastAPI(title ="Coursellm API")

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

@app.get("/")
def read_root():
    return {"message": "Welcome to the World Best Coursellm API!"}
