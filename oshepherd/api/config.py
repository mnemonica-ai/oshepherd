from pydantic import BaseModel
from typing import Optional
from multiprocessing import cpu_count


class ApiConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    HOST: Optional[str] = "0.0.0.0"
    PORT: Optional[int] = 5001
    WORKERS: Optional[int] = max(1, cpu_count() - 1)
