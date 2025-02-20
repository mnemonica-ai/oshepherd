from datetime import datetime, timedelta, timezone
import json
from redis import Redis
from oshepherd.api.config import ApiConfig

OSHEPHERD_WORKERS_PATTERN = "oshepherd_worker:*"
OSHEPHERD_IDLE_WORKER_DELTA = 60  # secs


class NetworkData:

    def __init__(self, config: ApiConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.redis_client = Redis.from_url(self.backend_url)
        self.workers_pattern = OSHEPHERD_WORKERS_PATTERN
        self.idle_worker_delta = OSHEPHERD_IDLE_WORKER_DELTA

    def get_version(self):
        for key in self.redis_client.scan_iter(match=self.workers_pattern):
            data = self.redis_client.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            version = decoded_data.get("version")
            if version:
                return json.loads(version)

        return {}

    def get_available_tags(self):
        tags_dict = {}
        tags_res = {"models": []}

        for key in self.redis_client.scan_iter(match=self.workers_pattern):
            data = self.redis_client.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            worker_id = decoded_data.get("worker_id")
            heartbeat = decoded_data.get("heartbeat")
            if not heartbeat:
                print(f" * worker [{worker_id}] has no heartbeat, skipped")
                continue

            heartbeat_time = datetime.fromisoformat(heartbeat)
            current_time = datetime.now(timezone.utc)
            if current_time - heartbeat_time > timedelta(
                seconds=self.idle_worker_delta
            ):
                print(f" * worker [{worker_id}] is idle, skipped")
                continue

            tags = json.loads(decoded_data.get("tags", "[]"))
            for model in tags.get("models", []):
                model_name = model.get("name")
                tags_dict[model_name] = model

        tags_res["models"] = list(tags_dict.values())
        return tags_res
