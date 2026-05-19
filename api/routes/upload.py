from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from api.routes.auth import CurrentUserDep, SessionDep
from models.document import Document
import os
import shutil

router = APIRouter()

@router.post("/")
async def upload_document(
    current_user: CurrentUserDep,
    session: SessionDep,
    file: UploadFile = File(...),
    course_id: int = Form(...)
):
    """
    Upload a document for a specific course.
    """
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = f"{upload_dir}/{file.filename}"
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}")
        
    db_document = Document(
        course_id=course_id,
        user_id=current_user.id,
        filename=file.filename
    )
    session.add(db_document)
    session.commit()
    session.refresh(db_document)
    
    return {"message": "File uploaded successfully", "document": db_document}
