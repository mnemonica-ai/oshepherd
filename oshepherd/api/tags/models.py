from pydantic import BaseModel
from typing import List
from datetime import datetime


class TagDetails(BaseModel):
    format: str
    family: str
    families: str
    parameter_size: str
    quantization_level: str


class Tag(BaseModel):
    name: str
    modified_at: datetime
    size: int
    digest: str
    details: TagDetails


class TagsResponse(BaseModel):
    models: List[Tag]
