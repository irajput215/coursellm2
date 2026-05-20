from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from sqlmodel import select
from api.routes.auth import CurrentUserDep, SessionDep
from models.document import Document
from models.course import Course
from rag.ingestion.pipeline import run_ingestion_pipeline
import os
import shutil

router = APIRouter()

@router.post("/")
async def upload_document(
    current_user: CurrentUserDep,
    session: SessionDep,
    file: UploadFile = File(...),
    course_name: str = Form(...)
):
    """
    Upload a document for a specific course by name.
    If the course doesn't exist, it will be created automatically.
    """
    # Check if a document with this filename already exists for the user
    existing_doc = session.exec(
        select(Document).where(Document.filename == file.filename, Document.user_id == current_user.id)
    ).first()
    
    if existing_doc:
        raise HTTPException(status_code=409, detail="A file with this name has already been uploaded.")

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = f"{upload_dir}/{file.filename}"
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}")
        
    # Find or create the course
    course = session.exec(select(Course).where(Course.name == course_name, Course.user_id == current_user.id)).first()
    if not course:
        course = Course(name=course_name, user_id=current_user.id)
        session.add(course)
        session.commit()
        session.refresh(course)
        
    db_document = Document(
        course_id=course.id,
        user_id=current_user.id,
        filename=file.filename
    )
    session.add(db_document)
    session.commit()
    session.refresh(db_document)
    
    # Run the ingestion pipeline synchronously
    try:
        db_chunks = run_ingestion_pipeline(
            file_path=file_path, 
            document_id=db_document.id, 
            course_id=course.id, 
            user_id=current_user.id
        )
        
        # Save chunks to the database
        if db_chunks:
            session.add_all(db_chunks)
            session.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion pipeline failed: {e}")
    
    return {
        "message": "File uploaded and processed successfully", 
        "document": db_document,
        "chunks_processed": len(db_chunks) if 'db_chunks' in locals() else 0
    }
