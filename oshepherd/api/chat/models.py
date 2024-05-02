from pydantic import BaseModel, HttpUrl
from typing import List, Optional
from datetime import datetime


class Message(BaseModel):
    role: str
    content: str
    images: Optional[List[HttpUrl]] = None


class ChatRequestPayload(BaseModel):
    model: str
    messages: Optional[List[Message]] = None
    format: Optional[str] = ""
    options: Optional[dict] = {}
    stream: Optional[bool] = False
    keep_alive: Optional[str] = None


class ChatRequest(BaseModel):
    type: str = "chat"
    payload: ChatRequestPayload


class ChatResponse(BaseModel):
    model: str
    created_at: datetime
    message: Message
    done: bool
    total_duration: int
    load_duration: int
    prompt_eval_count: Optional[int] = None
    prompt_eval_duration: int
    eval_count: int
    eval_duration: int
