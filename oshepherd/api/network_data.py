from datetime import datetime, timedelta, timezone
import json
import time
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from oshepherd.api.config import ApiConfig
from oshepherd.common.redis import get_socket_keepalive_options

OSHEPHERD_WORKERS_PATTERN = "oshepherd_worker:*"
OSHEPHERD_IDLE_WORKER_DELTA = 60  # secs


class NetworkData:

    def __init__(self, config: ApiConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.workers_pattern = OSHEPHERD_WORKERS_PATTERN
        self.idle_worker_delta = OSHEPHERD_IDLE_WORKER_DELTA

        print(f" > Network data using standard Redis connection")
        self.redis_client = Redis.from_url(
            self.backend_url,
            socket_keepalive=True,
            socket_keepalive_options=get_socket_keepalive_options(),
            retry_on_timeout=True,
            max_connections=10,
        )

    def get_version(self):
        try:
            for key in self.redis_client.scan_iter(match=self.workers_pattern):
                data = self.redis_client.hgetall(key)
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
        except Exception as e:
            print(f" >>> Failed to get version: {e}")
            return {}

    def get_available_tags(self):
        try:
            tags_dict = {}
            tags_res = {"models": []}

            for key in self.redis_client.scan_iter(match=self.workers_pattern):
                data = self.redis_client.hgetall(key)
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
        except Exception as e:
            print(f" >>> Failed to get tags: {e}")
            return {"models": []}

    def is_worker_idle(self, heartbeat):
        heartbeat_time = datetime.fromisoformat(heartbeat)
        current_time = datetime.now(timezone.utc)
        return current_time - heartbeat_time > timedelta(seconds=self.idle_worker_delta)
