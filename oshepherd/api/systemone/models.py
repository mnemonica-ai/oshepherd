from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SystemOneRequestPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, Any]


class SystemOneRequest(BaseModel):
    type: Literal["systemone"] = "systemone"
    payload: SystemOneRequestPayload
