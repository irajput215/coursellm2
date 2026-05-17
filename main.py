from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from api.routes import auth
from database import get_db, engine, Base


#create tables
Base.metadata.create_all(bind=engine)


app = FastAPI(title ="Coursellm API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])


@app.get("/")
def read_root():
    return {"message": "Welcome to the Coursellm API!"}
