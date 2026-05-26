from sqlmodel import Session
from rag.retrieval.semantic_search import SemanticRetriever
from rag.retrieval.keyword_search import KeywordRetriever
from rag.retrieval.schemas import SearchResult
from models.chunk import Chunk

class HybridRetriever:
    def __init__(self, session: Session):
        self.semantic = SemanticRetriever(session)
        self.keyword = KeywordRetriever(session)
        
    def _normalize(self, results: list[SearchResult], invert: bool = False) -> list[SearchResult]:
        if not results:
            return []
        
        scores = [r.score for r in results]
        min_score, max_score = min(scores), max(scores)
        
        normalized = []
        for r in results:
            if max_score == min_score:
                norm_score = 1.0 # If all scores are identical, assign max relevance
            else:
                norm_score = (r.score - min_score) / (max_score - min_score)
                
            if invert:
                # Lower score (distance) is better in semantic search, invert to match keyword
                norm_score = 1.0 - norm_score
                
            normalized.append(SearchResult(chunk=r.chunk, score=norm_score))
            
        return normalized

    def search(self, question: str, user_id: int, course_id: int | None = None, top_k: int = 5) -> list[SearchResult]:
        # 1. Fetch from both
        semantic_raw = self.semantic.search(question, user_id, course_id, top_k=top_k*2)
        keyword_raw = self.keyword.search(question, user_id, course_id, top_k=top_k*2)
        
        # 2. Normalize
        semantic_norm = self._normalize(semantic_raw, invert=True) 
        keyword_norm = self._normalize(keyword_raw, invert=False)
        
        # 3. Merge & Deduplicate
        merged_scores = {}
        SEMANTIC_WEIGHT = 0.7
        KEYWORD_WEIGHT = 0.3
        
        for res in semantic_norm:
            chunk_id = res.chunk.id
            if chunk_id not in merged_scores:
                merged_scores[chunk_id] = SearchResult(chunk=res.chunk, semantic_score=0.0, keyword_score=0.0, final_score=0.0)
            merged_scores[chunk_id].semantic_score = res.score
            merged_scores[chunk_id].final_score += res.score * SEMANTIC_WEIGHT
            
        for res in keyword_norm:
            chunk_id = res.chunk.id
            if chunk_id not in merged_scores:
                merged_scores[chunk_id] = SearchResult(chunk=res.chunk, semantic_score=0.0, keyword_score=0.0, final_score=0.0)
            merged_scores[chunk_id].keyword_score = res.score
            merged_scores[chunk_id].final_score += res.score * KEYWORD_WEIGHT
            
        # 4. Sort and Return Top K
        final_results = sorted(merged_scores.values(), key=lambda x: x.final_score, reverse=True)
        return final_results[:top_k]
