from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TagDetails(BaseModel):
    format: Optional[str]
    family: Optional[str]
    families: Optional[str]
    parameter_size: Optional[str]
    quantization_level: Optional[str]


class Tag(BaseModel):
    name: str
    modified_at: datetime
    size: int
    digest: str
    details: TagDetails


class TagsResponse(BaseModel):
    models: List[Tag]
