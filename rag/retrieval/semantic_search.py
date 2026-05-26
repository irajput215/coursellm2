from sqlmodel import Session, select
from models.chunk import Chunk
from rag.ingestion.embedder import Embedder
from rag.retrieval.schemas import SearchResult

class SemanticRetriever:
    def __init__(self, session: Session):
        self.session = session
        self.embedder = Embedder.get_instance()
        
    def search(self, question: str, user_id: int, course_id: int | None = None, top_k: int = 5) -> list[SearchResult]:
        query_embedding = self.embedder.generate_embeddings([question])[0]
        
        stmt = select(Chunk, Chunk.embedding.cosine_distance(query_embedding).label("distance")).where(Chunk.user_id == user_id)
        
        if course_id is not None:
            stmt = stmt.where(Chunk.course_id == course_id)
            
        stmt = stmt.order_by("distance").limit(top_k)
        
        results = self.session.exec(stmt).all()
        
        search_results = []
        for row in results:
            chunk, distance = row
            search_results.append(SearchResult(chunk=chunk, score=distance))
            
        return search_results
