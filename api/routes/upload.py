from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from sqlmodel import select, delete
from api.routes.auth import CurrentUserDep, SessionDep
from models.document import Document
from models.course import Course
from rag.ingestion.pipeline import run_ingestion_pipeline
from workers.orchestrator import process_document_pipeline
from models.generation import GenerationStatus
from models.knowledge import DocumentTopic, GeneratedNote
from models.chunk import Chunk
import os
import shutil

router = APIRouter()

class RawTextUpload(BaseModel):
    course_name: str
    document_name: str
    text_content: str

@router.post("/text")
async def upload_raw_text(
    request: RawTextUpload,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Upload raw text directly without a file. 
    It creates a .txt file under the hood and runs the ingestion pipeline.
    """
    filename = request.document_name
    if not filename.endswith('.txt'):
        filename += '.txt'
        
    existing_doc = session.exec(
        select(Document).where(Document.filename == filename, Document.user_id == current_user.id)
    ).first()
    
    if existing_doc:
        raise HTTPException(status_code=409, detail="A file with this name has already been uploaded.")
        
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = f"{upload_dir}/{filename}"
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(request.text_content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save text file: {e}")
        
    course = session.exec(select(Course).where(Course.name == request.course_name, Course.user_id == current_user.id)).first()
    if not course:
        course = Course(name=request.course_name, user_id=current_user.id)
        session.add(course)
        session.commit()
        session.refresh(course)
        
    db_document = Document(
        course_id=course.id,
        user_id=current_user.id,
        filename=filename
    )
    session.add(db_document)
    session.commit()
    session.refresh(db_document)
    
    try:
        db_chunks = run_ingestion_pipeline(
            file_path=file_path, 
            document_id=db_document.id, 
            course_id=course.id, 
            user_id=current_user.id
        )
        
        if db_chunks:
            session.add_all(db_chunks)
            session.commit()
            
        process_document_pipeline.send(db_document.id)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion pipeline failed: {e}")
    
    return {
        "message": "Text uploaded and processed successfully. Notes generation started in background.", 
        "document": db_document,
        "chunks_processed": len(db_chunks) if 'db_chunks' in locals() else 0
    }

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
            
        # Trigger background knowledge generation pipeline
        process_document_pipeline.send(db_document.id)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion pipeline failed: {e}")
    
    return {
        "message": "File uploaded and processed successfully. Notes generation started in background.", 
        "document": db_document,
        "chunks_processed": len(db_chunks) if 'db_chunks' in locals() else 0
    }

@router.get("/generation-status")
async def get_generation_status(
    course_name: str,
    document_name: str,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Get the status of the background knowledge generation pipeline.
    """
    course = session.exec(
        select(Course).where(Course.name == course_name, Course.user_id == current_user.id)
    ).first()
    
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    doc = session.exec(
        select(Document).where(Document.filename == document_name, Document.course_id == course.id)
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found in this course")
        
    statuses = session.exec(
        select(GenerationStatus).where(GenerationStatus.document_id == doc.id)
    ).all()
    
    tasks = [{"task_type": s.task_type, "status": s.status, "error_message": s.error_message} for s in statuses]
    
    # Simple overall status logic
    overall_status = "pending"
    if tasks:
        if any(t["status"] == "failed" for t in tasks):
            overall_status = "failed"
        elif all(t["status"] == "completed" for t in tasks):
            overall_status = "completed"
        elif any(t["status"] == "processing" for t in tasks):
            overall_status = "processing"
            
    return {
        "document_name": document_name,
        "course_name": course_name,
        "overall_status": overall_status,
        "tasks": tasks
    }

@router.get("/notes")
async def get_generated_notes(
    course_name: str,
    document_name: str,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Retrieve all extracted topics and generated notes for a specific document.
    """
    course = session.exec(
        select(Course).where(Course.name == course_name, Course.user_id == current_user.id)
    ).first()
    
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    doc = session.exec(
        select(Document).where(Document.filename == document_name, Document.course_id == course.id)
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found in this course")
        
    topics = session.exec(
        select(DocumentTopic).where(DocumentTopic.document_id == doc.id).order_by(DocumentTopic.topic_order)
    ).all()
    
    notes = session.exec(
        select(GeneratedNote).where(GeneratedNote.document_id == doc.id)
    ).all()
    
    return {
        "document_name": document_name,
        "course_name": course_name,
        "topics": [t.topic_name for t in topics],
        "notes": notes
    }

@router.delete("/")
async def delete_document(
    course_name: str,
    document_name: str,
    current_user: CurrentUserDep,
    session: SessionDep
):
    """
    Delete a document and all of its generated background knowledge and chunks.
    """
    course = session.exec(
        select(Course).where(Course.name == course_name, Course.user_id == current_user.id)
    ).first()
    
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    doc = session.exec(
        select(Document).where(Document.filename == document_name, Document.course_id == course.id)
    ).first()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found in this course")
        
    # Delete dependent data
    session.exec(delete(GenerationStatus).where(GenerationStatus.document_id == doc.id))
    session.exec(delete(DocumentTopic).where(DocumentTopic.document_id == doc.id))
    session.exec(delete(GeneratedNote).where(GeneratedNote.document_id == doc.id))
    session.exec(delete(Chunk).where(Chunk.document_id == doc.id))
    
    # Delete document record
    session.delete(doc)
    session.commit()
    
    # Try to delete the physical file
    file_path = f"uploads/{document_name}"
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError as e:
            # Non-fatal if file already gone or locked
            print(f"Error removing physical file {file_path}: {e}")
            
    return {"message": f"Document '{document_name}' and all associated data have been permanently deleted."}
