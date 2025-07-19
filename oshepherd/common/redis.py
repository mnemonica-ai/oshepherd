from datetime import datetime, timedelta, timezone
import json
import time
import socket
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError


def get_socket_keepalive_options():
    """Get platform-compatible socket keepalive options."""
    options = {}

    # Linux specific constants
    if hasattr(socket, "TCP_KEEPINTVL"):
        options[socket.TCP_KEEPINTVL] = 1
    if hasattr(socket, "TCP_KEEPCNT"):
        options[socket.TCP_KEEPCNT] = 3
    if hasattr(socket, "TCP_KEEPIDLE"):
        options[socket.TCP_KEEPIDLE] = 1

    return options


class RedisClient:
    def __init__(self, backend_url: str, connection_recycling: bool = False):
        self.backend_url = backend_url
        self.connection_recycling = connection_recycling

        if self.connection_recycling:
            print(f" > Redis using connection recycling mode")
            self.client = None
        else:
            print(f" > Redis using persistent connection mode")
            self.client = Redis.from_url(
                self.backend_url,
                socket_keepalive=True,
                socket_keepalive_options=get_socket_keepalive_options(),
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=10,
            )

    def _get_fresh_client(self):
        """Create a fresh Redis client for recycling mode"""
        return Redis.from_url(
            self.backend_url,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
            max_connections=1,
        )

    def _execute_with_retry(self, operation, max_retries=3):
        """Execute Redis operation with retry logic for persistent mode"""
        for attempt in range(max_retries):
            try:
                return operation()
            except RedisConnectionError as e:
                print(
                    f" >>> Redis connection error (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    # Reset connection on error
                    if self.client:
                        self.client.connection_pool.reset()
                    time.sleep(1)
                else:
                    print(
                        f" >>> Failed to execute Redis operation after {max_retries} attempts"
                    )
                    raise

    def execute_operation(self, operation):
        """
        Execute operation with appropriate connection strategy:
        - Recycling mode: create fresh connection for each operation
        - Persistent mode: use retry logic
        """
        if self.connection_recycling:
            redis_client = self._get_fresh_client()
            try:
                return operation(redis_client)
            except RedisConnectionError as e:
                print(f" >>> Redis connection error (recycling mode): {e}")
                return None
            finally:
                redis_client.close()
        else:
            return self._execute_with_retry(lambda: operation(self.client))

    def hset(self, name, key=None, value=None, mapping=None):
        """Set one or more hash fields to their respective values"""

        def _hset_operation(redis_client):
            return redis_client.hset(name, key=key, value=value, mapping=mapping)

        if self.connection_recycling:
            return self.execute_operation(_hset_operation)
        else:
            return self._execute_with_retry(
                lambda: self.client.hset(name, key=key, value=value, mapping=mapping)
            )

    def hgetall(self, name):
        """Return all fields and values in a hash"""

        def _hgetall_operation(redis_client):
            return redis_client.hgetall(name)

        return self.execute_operation(_hgetall_operation)

    def scan_iter(self, match=None, count=None):
        """Return an iterator for all keys matching the pattern"""

        def _scan_iter_operation(redis_client):
            return list(redis_client.scan_iter(match=match, count=count))

        result = self.execute_operation(_scan_iter_operation)
        return result if result is not None else []

    def xread(self, streams, count=None, block=None):
        """Read from Redis streams"""

        def _xread_operation(redis_client):
            return redis_client.xread(streams, count=count, block=block)

        if self.connection_recycling:
            return self.execute_operation(_xread_operation)
        else:
            return self._execute_with_retry(
                lambda: self.client.xread(streams, count=count, block=block)
            )

    def close(self):
        """Close the Redis connection"""
        if self.client and not self.connection_recycling:
            self.client.close()
