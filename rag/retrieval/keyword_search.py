from sqlmodel import Session
from sqlalchemy import text
from models.chunk import Chunk
from rag.retrieval.schemas import SearchResult

class KeywordRetriever:
    def __init__(self, session: Session):
        self.session = session
        
    def search(self, question: str, user_id: int, course_id: int | None = None, top_k: int = 5) -> list[SearchResult]:
        query_text = """
            SELECT *,
                   ts_rank(to_tsvector('english', content), plainto_tsquery('english', :question)) AS keyword_score
            FROM chunks
            WHERE user_id = :user_id
              AND to_tsvector('english', content) @@ plainto_tsquery('english', :question)
        """
        params = {"question": question, "user_id": user_id}
        
        if course_id is not None:
            query_text += " AND course_id = :course_id"
            params["course_id"] = course_id
            
        query_text += " ORDER BY keyword_score DESC LIMIT :top_k"
        params["top_k"] = top_k
        
        results = self.session.execute(text(query_text), params).mappings().all()
        
        search_results = []
        for row in results:
            # Reconstruct the Chunk model from the raw mapping
            row_dict = dict(row)
            # Remove the extra generated column before instantiating Chunk
            score = row_dict.pop("keyword_score")
            chunk = Chunk(**row_dict)
            search_results.append(SearchResult(chunk=chunk, score=score))
            
        return search_results
