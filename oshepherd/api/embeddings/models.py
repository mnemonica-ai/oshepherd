from typing import List, Optional
from pydantic import BaseModel


class EmbeddingsPayload(BaseModel):
    model: str
    prompt: str
    options: Optional[dict] = None
    keep_alive: Optional[str] = "5m"


class EmbeddingsRequest(BaseModel):
    type: str = "embeddings"
    payload: EmbeddingsPayload


class EmbeddingsResponse(BaseModel):
    embedding: List[float]
