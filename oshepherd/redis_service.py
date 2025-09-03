import threading
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError


class RedisService:
    """
    Resilient Redis connection handler with automatic reconnection.
    """

    def __init__(self, backend_url: str):
        self.backend_url = backend_url
        self.redis_client = self._create_redis_client()
        self._connection_lock = threading.Lock()

    def _create_redis_client(self):
        """Create Redis client with optimal settings for reliability."""
        return Redis.from_url(
            self.backend_url,
            socket_keepalive=True,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            max_connections=10,
        )

    def _ensure_connection(self):
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

    def _with_retry(self, operation, *args, **kwargs):
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

    def hset(self, name, key=None, value=None, mapping=None):
        """Redis HSET with automatic retry."""
        return self._with_retry(
            self.redis_client.hset, name, key, value, mapping=mapping
        )

    def hgetall(self, name):
        """Redis HGETALL with automatic retry."""
        return self._with_retry(self.redis_client.hgetall, name)

    def scan_iter(self, **kwargs):
        """Redis SCAN_ITER with automatic retry."""
        return self._with_retry(self.redis_client.scan_iter, **kwargs)

    def ping(self):
        """Redis PING with automatic retry."""
        return self._with_retry(self.redis_client.ping)
