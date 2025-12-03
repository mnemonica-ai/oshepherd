"""
Show Model Information
Data models for `POST /api/show` endpoint request and response.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information
"""

from pydantic import BaseModel
from typing import Optional


class ShowRequest(BaseModel):
    """Request model for showing model information."""

    model: str
    verbose: Optional[bool] = False
