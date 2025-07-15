from datetime import datetime, timedelta, timezone
import json
import socket
import time
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from oshepherd.api.config import ApiConfig

OSHEPHERD_WORKERS_PATTERN = "oshepherd_worker:*"
OSHEPHERD_IDLE_WORKER_DELTA = 60  # secs


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


class NetworkData:

    def __init__(self, config: ApiConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.workers_pattern = OSHEPHERD_WORKERS_PATTERN
        self.idle_worker_delta = OSHEPHERD_IDLE_WORKER_DELTA
        self.connection_recycling = config.REDIS_CONNECTION_RECYCLING

        if self.connection_recycling:
            print(f" > Network data using connection recycling mode")
            self.redis_client = None
        else:
            print(f" > Network data using persistent connection mode")
            self.redis_client = Redis.from_url(
                self.backend_url,
                socket_keepalive=True,
                socket_keepalive_options=_get_socket_keepalive_options(),
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=10,
            )

    def _get_fresh_redis_client(self):
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
                    if self.redis_client:
                        self.redis_client.connection_pool.reset()
                    time.sleep(1)
                else:
                    print(
                        f" >>> Failed to execute Redis operation after {max_retries} attempts"
                    )
                    raise

    def _execute_operation(self, operation):
        """
        Execute operation with appropriate connection strategy:
        - Recycling mode: create fresh connection for each operation
        - Persistent mode: use retry logic
        """

        if self.connection_recycling:
            redis_client = self._get_fresh_redis_client()
            try:
                return operation(redis_client)
            except RedisConnectionError as e:
                print(f" >>> Redis connection error (recycling mode): {e}")
                return None
            finally:
                redis_client.close()
        else:
            return self._execute_with_retry(lambda: operation(self.redis_client))

    def get_version(self):
        def _get_version_operation(redis_client):
            for key in redis_client.scan_iter(match=self.workers_pattern):
                data = redis_client.hgetall(key)
                decoded_data = {
                    k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
                }

                worker_id = decoded_data.get("worker_id")
                heartbeat = decoded_data.get("heartbeat")
                if not heartbeat:
                    print(f" * worker [{worker_id}] has no heartbeat, skipped")
                    continue

                if self.is_worker_idle(heartbeat):
                    print(f" * worker [{worker_id}] is idle, skipped")
                    continue

                version = decoded_data.get("version")
                if version:
                    return json.loads(version)
            return {}

        try:
            result = self._execute_operation(_get_version_operation)
            return result if result is not None else {}
        except Exception as e:
            print(f" >>> Failed to get version: {e}")
            return {}

    def get_available_tags(self):
        def _get_tags_operation(redis_client):
            tags_dict = {}
            tags_res = {"models": []}

            for key in redis_client.scan_iter(match=self.workers_pattern):
                data = redis_client.hgetall(key)
                decoded_data = {
                    k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
                }

                worker_id = decoded_data.get("worker_id")
                heartbeat = decoded_data.get("heartbeat")
                if not heartbeat:
                    print(f" * worker [{worker_id}] has no heartbeat, skipped")
                    continue

                if self.is_worker_idle(heartbeat):
                    print(f" * worker [{worker_id}] is idle, skipped")
                    continue

                tags = json.loads(decoded_data.get("tags", "[]"))
                for model in tags.get("models", []):
                    model_name = model.get("name")
                    tags_dict[model_name] = model

            tags_res["models"] = list(tags_dict.values())
            return tags_res

        try:
            result = self._execute_operation(_get_tags_operation)
            return result if result is not None else {"models": []}
        except Exception as e:
            print(f" >>> Failed to get tags: {e}")
            return {"models": []}

    def is_worker_idle(self, heartbeat):
        heartbeat_time = datetime.fromisoformat(heartbeat)
        current_time = datetime.now(timezone.utc)
        return current_time - heartbeat_time > timedelta(seconds=self.idle_worker_delta)
