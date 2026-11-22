from typing import List, Optional
from pydantic import AliasChoices, BaseModel, Field


class EmbeddingsPayload(BaseModel):
    model: str
    input: str = Field(validation_alias=AliasChoices("input", "prompt"))
    options: Optional[dict] = None
    keep_alive: Optional[str] = "5m"


class EmbeddingsRequest(BaseModel):
    type: str = "embeddings"
    payload: EmbeddingsPayload


class EmbeddingsResponse(BaseModel):
    embedding: List[float]
