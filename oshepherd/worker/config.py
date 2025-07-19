from pydantic import BaseModel
from typing import Optional
from oshepherd.common.redis import get_socket_keepalive_options


class WorkerConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_SOCKET_KEEPALIVE: bool = True
    HEALTH_CHECK_INTERVAL: Optional[int] = 30
    MAX_RESTART_ATTEMPTS: Optional[int] = 10

    BROKER_TRANSPORT_OPTIONS: Optional[dict] = {
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.5,
        "retry_on_timeout": True,
        "socket_connect_timeout": 10,
        "socket_timeout": 30,
        "socket_keepalive": True,
        "socket_keepalive_options": get_socket_keepalive_options(),
    }
