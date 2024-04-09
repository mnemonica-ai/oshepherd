from pydantic import BaseModel
from typing import Optional


class WorkerConfig(BaseModel):
    RABBITMQ_URL: str
    REDIS_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
