from typing import List, Optional
from pydantic import BaseModel


class TagsResponse(BaseModel):
    models: List[dict]
