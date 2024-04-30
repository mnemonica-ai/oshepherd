from pydantic import BaseModel, HttpUrl
from typing import List, Optional


class Message(BaseModel):
    role: str
    content: str
    images: Optional[List[HttpUrl]] = None


class ChatRequestPayload(BaseModel):
    model: str  # Required model name
    messages: Optional[List[Message]] = None
    format: Optional[str] = "json"
    options: Optional[dict] = None
    stream: Optional[bool] = None
    keep_alive: Optional[str] = "5m"


class ChatRequest(BaseModel):
    type: str
    payload: ChatRequestPayload


# TODO add response type
