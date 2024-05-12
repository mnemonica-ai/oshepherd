from pydantic import BaseModel
from typing import Optional, List, Literal
from datetime import datetime


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    # TODO add support for images
    # images: NotRequired[Sequence[Any]]


class ChatRequestPayload(BaseModel):
    model: str
    messages: Optional[List[ChatMessage]] = None
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
    message: ChatMessage
    done: bool
    total_duration: int
    load_duration: int
    prompt_eval_count: Optional[int] = None
    prompt_eval_duration: int
    eval_count: int
    eval_duration: int
