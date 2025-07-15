from pydantic import BaseModel
from typing import Optional
import socket


def _get_socket_keepalive_options():
    """Get platform-compatible socket keepalive options."""
    options = {}

    # Linux specific contants
    # TODO abstract this to a common function
    if hasattr(socket, "TCP_KEEPINTVL"):
        options[socket.TCP_KEEPINTVL] = 1
    if hasattr(socket, "TCP_KEEPCNT"):
        options[socket.TCP_KEEPCNT] = 3
    if hasattr(socket, "TCP_KEEPIDLE"):
        options[socket.TCP_KEEPIDLE] = 1

    return options


class WorkerConfig(BaseModel):
    CELERY_BROKER_URL: str
    CELERY_BACKEND_URL: str
    LOGLEVEL: Optional[str] = "info"
    CONCURRENCY: Optional[int] = 1
    PREFETCH_MULTIPLIER: Optional[int] = 1
    RESULTS_EXPIRES: Optional[int] = 3600
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_SOCKET_KEEPALIVE: bool = True

    # Connection recycling options
    REDIS_CONNECTION_RECYCLING: Optional[bool] = False
    REDIS_HEARTBEAT_INTERVAL: Optional[int] = 10  # seconds

    # Standard broker transport options (for persistent connections)
    BROKER_TRANSPORT_OPTIONS: Optional[dict] = {
        "max_retries": 5,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.5,
        "retry_on_timeout": True,
        "socket_connect_timeout": 30,
        "socket_timeout": 30,
        "socket_keepalive": True,
        "socket_keepalive_options": _get_socket_keepalive_options(),
        "health_check_interval": 30,
    }

    # Recycling mode broker transport options (for small Redis servers)
    BROKER_TRANSPORT_OPTIONS_RECYCLING: Optional[dict] = {
        "visibility_timeout": 300,  # 5 minutes
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.1,
        "interval_max": 0.5,
        "retry_on_timeout": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
        "socket_keepalive": False,
        "socket_keepalive_options": {},
        "health_check_interval": 0,
    }
