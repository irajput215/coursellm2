import time

from sqlmodel import Session, select
from models.chunk import Chunk
from rag.ingestion.embedder import Embedder
from rag.retrieval.schemas import SearchResult
from observability.tracing.langfuse_client import log_pipeline_event, trace_span

class SemanticRetriever:
    def __init__(self, session: Session):
        self.session = session
        self.embedder = Embedder.get_instance()
        
    def search(self, question: str, user_id: int, course_id: int | None = None, top_k: int = 5) -> list[SearchResult]:
        start = time.perf_counter()
        with trace_span(
            "semantic_search",
            as_type="retriever",
            input={"query": question, "top_k": top_k},
        ) as span:
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

            chunk_ids = [result.chunk.id for result in search_results]
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            span.update(
                output={"chunk_ids": chunk_ids, "count": len(search_results)},
                metadata={"latency_ms": str(latency_ms)},
            )
            log_pipeline_event(
                "semantic_search",
                query=question,
                semantic_chunks=chunk_ids,
                count=len(search_results),
                latency_ms=latency_ms,
            )
            
        return search_results
