from rag.ingestion.parser import parse_document
from rag.ingestion.chunker import SemanticChunker
from rag.ingestion.embedder import Embedder
from models.chunk import Chunk

def run_ingestion_pipeline(file_path: str, document_id: int, course_id: int, user_id: int) -> list[Chunk]:
    """
    Executes the full RAG ingestion pipeline on a given file.
    Returns a list of Chunk models ready to be inserted into the database.
    """
    # 1. Parse Document
    pages_data = parse_document(file_path)
    
    # 2. Semantic Chunking
    chunker = SemanticChunker()
    raw_chunks = chunker.chunk_document(pages_data, document_id, course_id, user_id)
    
    if not raw_chunks:
        return []
        
    # 3. Generate Embeddings
    embedder = Embedder.get_instance()
    texts_to_embed = [chunk["content"] for chunk in raw_chunks]
    embeddings = embedder.generate_embeddings(texts_to_embed)
    
    # 4. Create Chunk Models
    db_chunks = []
    for raw_chunk, embedding in zip(raw_chunks, embeddings):
        db_chunk = Chunk(
            document_id=raw_chunk["document_id"],
            course_id=raw_chunk["course_id"],
            user_id=raw_chunk["user_id"],
            content=raw_chunk["content"],
            page=raw_chunk["page"],
            topic=raw_chunk["topic"],
            chunk_index=raw_chunk["chunk_index"],
            embedding=embedding
        )
        db_chunks.append(db_chunk)
        
    return db_chunks
