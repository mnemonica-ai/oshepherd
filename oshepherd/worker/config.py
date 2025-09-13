from pydantic import BaseModel
from typing import Optional


class WorkerConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
    # Performance tuning options
    ENABLE_COMPRESSION: Optional[bool] = False
    CONNECTION_POOL_SIZE: Optional[int] = 3
    REDIS_SOCKET_KEEPALIVE_IDLE: Optional[int] = 240
    REDIS_SOCKET_KEEPALIVE_INTERVAL: Optional[int] = 15
    REDIS_SOCKET_KEEPALIVE_COUNT: Optional[int] = 3
    BROKER_TRANSPORT_OPTIONS: Optional[dict] = {
        "max_retries": 10,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 2.0,
        "socket_keepalive": True,
        "retry_on_timeout": True,
        # Having in mind 30 connection limit
        "max_connections": 3,
        "socket_timeout": 30,
        "socket_connect_timeout": 5,
        "health_check_interval": 60,
        "socket_keepalive_options": {},
    }
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_SOCKET_KEEPALIVE: bool = True
