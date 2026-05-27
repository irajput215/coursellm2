import time

from sentence_transformers import CrossEncoder
from rag.retrieval.schemas import SearchResult
from observability.tracing.langfuse_client import log_pipeline_event, trace_span

class Reranker:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        self.model = CrossEncoder("BAAI/bge-reranker-base")
        
    def rerank(self, query: str, results: list[SearchResult], top_k: int = 5) -> list[SearchResult]:
        if not results:
            return []

        start = time.perf_counter()
        with trace_span(
            "rerank",
            as_type="chain",
            input={"query": query, "candidate_count": len(results), "top_k": top_k},
        ) as span:
            pairs = [[query, r.chunk.content] for r in results]
            scores = self.model.predict(pairs)
            
            for idx, score in enumerate(scores):
                results[idx].rerank_score = float(score)
                
            final_results = sorted(results, key=lambda x: x.rerank_score, reverse=True)
            top_results = final_results[:top_k]

            chunk_ids = [result.chunk.id for result in top_results]
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            span.update(
                output={"chunk_ids": chunk_ids, "count": len(top_results)},
                metadata={"latency_ms": str(latency_ms)},
            )
            log_pipeline_event(
                "rerank",
                query=query,
                input_chunks=[result.chunk.id for result in results],
                final_chunks=chunk_ids,
                latency_ms=latency_ms,
            )

        return top_results
