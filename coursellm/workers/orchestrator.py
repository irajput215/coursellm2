import dramatiq
from rag.ingestion.embedder import Embedder
import workers.base
from sqlmodel import Session, select
from database import engine
import models.user
import models.course
import models.document
import models.chunk
import models.chat
import models.generation
import models.knowledge
from models.generation import GenerationStatus
from rag.agents.topic_extractor import extract_topics
from rag.agents.notes_generator import generate_all_notes
import logging

logger = logging.getLogger(__name__)

def update_status(document_id: int, task_type: str, status: str, error_message: str = None):
    with Session(engine) as session:
        stmt = select(GenerationStatus).where(
            GenerationStatus.document_id == document_id,
            GenerationStatus.task_type == task_type
        )
        task_status = session.exec(stmt).first()
        
        if not task_status:
            task_status = GenerationStatus(document_id=document_id, task_type=task_type, status=status)
            session.add(task_status)
        else:
            task_status.status = status
            
        if error_message:
            task_status.error_message = error_message
            
        session.commit()

@dramatiq.actor(max_retries=3, min_backoff=15000)
def process_document_pipeline(document_id: int):
    try:
        update_status(document_id, 'pipeline', 'processing')
        
        # Step 1: Topics extraction
        update_status(document_id, 'topics', 'processing')
        topics = extract_topics(document_id)
        update_status(document_id, 'topics', 'completed')
        
        # Step 2: Notes generation
        update_status(document_id, 'notes', 'processing')
        generate_all_notes(document_id, topics)
        update_status(document_id, 'notes', 'completed')
        
        update_status(document_id, 'pipeline', 'completed')
        
    except Exception as e:
        logger.error(f"Pipeline failed for document {document_id}: {e}")
        update_status(document_id, 'pipeline', 'failed', str(e))
        raise e

@dramatiq.actor
def embed_texts(texts: list[str]):
    """Process embeddings in background"""
    embedder = Embedder.get_instance()
    return embedder.generate_embeddings(texts)
