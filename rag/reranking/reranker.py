from sentence_transformers import CrossEncoder
from rag.retrieval.schemas import SearchResult

class Reranker:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        # Initialize CrossEncoder using Singleton to avoid reloading model on every request
        self.model = CrossEncoder("BAAI/bge-reranker-base")
        
    def rerank(self, query: str, results: list[SearchResult], top_k: int = 5) -> list[SearchResult]:
        if not results:
            return []
            
        # Build pairs [query, document_text]
        pairs = [[query, r.chunk.content] for r in results]
        
        # Run predictions
        scores = self.model.predict(pairs)
        
        # Attach scores to our tracking objects
        for idx, score in enumerate(scores):
            results[idx].rerank_score = float(score)
            
        # Sort descending by the new rerank score
        final_results = sorted(results, key=lambda x: x.rerank_score, reverse=True)
        return final_results[:top_k]
