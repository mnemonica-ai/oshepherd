import threading
from typing import Any, Optional, Dict, Iterator
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError


class RedisService:
    """
    Resilient Redis service with automatic reconnection.
    """

    def __init__(self, backend_url: str) -> None:
        self.backend_url: str = backend_url
        self.redis_client: Redis = self._create_redis_client()
        self._connection_lock: threading.Lock = threading.Lock()

    def _create_redis_client(self) -> Redis:
        """Create Redis client with optimal settings for reliability."""
        return Redis.from_url(
            self.backend_url,
            socket_keepalive=True,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            max_connections=10,
        )

    def _ensure_connection(self) -> bool:
        """Ensure Redis connection is healthy, reconnect if needed."""
        try:
            self.redis_client.ping()
            return True
        except (
            RedisConnectionError,
            ConnectionResetError,
            TimeoutError,
            BrokenPipeError,
        ) as e:
            print(f" ! Redis connection lost: {e}")
            with self._connection_lock:
                try:
                    self.redis_client = self._create_redis_client()
                    self.redis_client.ping()
                    print(" > Redis connection restored")
                    return True
                except Exception as reconnect_error:
                    print(f" ! Failed to reconnect to Redis: {reconnect_error}")
                    return False

    def _with_retry(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute Redis operation with automatic retry on connection failure."""
        if not self._ensure_connection():
            raise RedisConnectionError("Redis connection unavailable")

        try:
            return operation(*args, **kwargs)
        except (
            RedisConnectionError,
            ConnectionResetError,
            TimeoutError,
            BrokenPipeError,
        ) as e:
            print(f" ! Redis operation failed: {e}")
            if self._ensure_connection():
                try:
                    return operation(*args, **kwargs)
                except Exception as retry_error:
                    print(f" ! Operation failed after reconnection: {retry_error}")
                    raise
            else:
                raise RedisConnectionError("Could not recover Redis connection")

    def hset(
        self,
        name: str,
        key: Optional[str] = None,
        value: Optional[Any] = None,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self._with_retry(
            self.redis_client.hset, name, key, value, mapping=mapping
        )

    def hgetall(self, name: str) -> Dict[str, str]:
        return self._with_retry(self.redis_client.hgetall, name)

    def scan_iter(self, **kwargs: Any) -> Iterator[str]:
        return self._with_retry(self.redis_client.scan_iter, **kwargs)

    def ping(self) -> bool:
        return self._with_retry(self.redis_client.ping)
