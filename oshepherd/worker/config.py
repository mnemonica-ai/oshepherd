from pydantic import BaseModel
from typing import Optional


class WorkerConfig(BaseModel):
    RABBITMQ_URL: str
    REDIS_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
    BROKER_TRANSPORT_OPTIONS: Optional[dict] = {
        "max_retries": 5,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.5,
    }
