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
        "max_retries": 10,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 2.0,
        "socket_keepalive": True,
        "retry_on_timeout": True,
        "max_connections": 5,
        "socket_timeout": 60,
        "socket_connect_timeout": 10,
        "health_check_interval": 30,
        "connection_pool_kwargs": {
            "retry_on_timeout": True,
            "socket_keepalive": True,
            "socket_keepalive_options": {},
        },
    }
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_SOCKET_KEEPALIVE: bool = True
