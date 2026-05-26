from pydantic import BaseModel, ConfigDict
from models.chunk import Chunk

class SearchResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    chunk: Chunk
    score: float | None = None
    semantic_score: float | None = None
    keyword_score: float | None = None
    final_score: float | None = None
    rerank_score: float | None = None
