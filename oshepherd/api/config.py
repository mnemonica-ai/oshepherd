from pydantic import BaseModel
from typing import Optional


class ApiConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    HOST: Optional[str] = "0.0.0.0"
    PORT: Optional[int] = 5001
