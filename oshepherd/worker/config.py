from pydantic import BaseModel
from typing import Optional


class WorkerConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
    BROKER_TRANSPORT_OPTIONS: Optional[dict] = {
        "max_retries": 5,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.5,
        "socket_keepalive": True,
        "retry_on_timeout": True,
        "max_connections": 10,
        "socket_timeout": 30,
        "socket_connect_timeout": 5,
    }
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_SOCKET_KEEPALIVE: bool = True
