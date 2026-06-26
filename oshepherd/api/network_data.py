from datetime import datetime, timedelta, timezone
import json
import logging
from oshepherd.api.config import ApiConfig
from oshepherd.common.redis_service import RedisService

OSHEPHERD_WORKERS_PATTERN = "oshepherd_worker:*"
OSHEPHERD_IDLE_WORKER_DELTA = 60  # secs
logger = logging.getLogger(__name__)


class NetworkData:

    def __init__(self, config: ApiConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.redis_service = RedisService(self.backend_url)
        self.workers_pattern = OSHEPHERD_WORKERS_PATTERN
        self.idle_worker_delta = OSHEPHERD_IDLE_WORKER_DELTA

    def get_version(self):
        for key in self.redis_service.scan_iter(match=self.workers_pattern):
            data = self.redis_service.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            worker_id = decoded_data.get("worker_id")
            heartbeat = decoded_data.get("heartbeat")
            if not heartbeat:
                logger.debug("worker has no heartbeat, skipped worker_id=%s", worker_id)
                continue

            if self.is_worker_idle(heartbeat):
                logger.debug("worker is idle, skipped worker_id=%s", worker_id)
                continue

            version = decoded_data.get("version")
            if version:
                return json.loads(version)

        return {}

    def get_available_tags(self):
        tags_dict = {}
        tags_res = {"models": []}

        for key in self.redis_service.scan_iter(match=self.workers_pattern):
            data = self.redis_service.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            worker_id = decoded_data.get("worker_id")
            heartbeat = decoded_data.get("heartbeat")
            if not heartbeat:
                logger.debug("worker has no heartbeat, skipped worker_id=%s", worker_id)
                continue

            if self.is_worker_idle(heartbeat):
                logger.debug("worker is idle, skipped worker_id=%s", worker_id)
                continue

            tags = json.loads(decoded_data.get("tags", "[]"))
            for model in tags.get("models", []):
                model_name = model.get("name")
                tags_dict[model_name] = model

        tags_res["models"] = list(tags_dict.values())
        return tags_res

    def get_running_models(self):
        """Get list of running models from all active workers."""

        models_list = []
        for key in self.redis_service.scan_iter(match=self.workers_pattern):
            data = self.redis_service.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            worker_id = decoded_data.get("worker_id")
            heartbeat = decoded_data.get("heartbeat")
            if not heartbeat:
                logger.debug("worker has no heartbeat, skipped worker_id=%s", worker_id)
                continue

            if self.is_worker_idle(heartbeat):
                logger.debug("worker is idle, skipped worker_id=%s", worker_id)
                continue

            ps_data = json.loads(decoded_data.get("ps", "{}"))
            if "models" in ps_data:
                models_list.extend(ps_data["models"])

        return {"models": models_list}

    def get_model_info(self, model_name):
        """Get show information for a specific model from any active worker."""

        for key in self.redis_service.scan_iter(match=self.workers_pattern):
            data = self.redis_service.hgetall(key)
            # Decode bytes to strings
            decoded_data = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in data.items()
            }

            worker_id = decoded_data.get("worker_id")
            heartbeat = decoded_data.get("heartbeat")
            if not heartbeat:
                logger.debug("worker has no heartbeat, skipped worker_id=%s", worker_id)
                continue

            if self.is_worker_idle(heartbeat):
                logger.debug("worker is idle, skipped worker_id=%s", worker_id)
                continue

            show_data_raw = decoded_data.get("show")
            if show_data_raw:
                try:
                    show_map = json.loads(show_data_raw)
                    if model_name in show_map:
                        model_info = show_map[model_name]
                        if not model_info.get("error"):
                            # Wrap model_info in the format expected by ollama.Client
                            return {"model_info": model_info}
                except json.JSONDecodeError:
                    logger.warning(
                        "worker has invalid show data, skipped worker_id=%s",
                        worker_id,
                    )
                    continue

        return {
            "error": "Model not found",
            "message": f"Model '{model_name}' not found on any active worker",
        }

    def is_worker_idle(self, heartbeat):
        heartbeat_time = datetime.fromisoformat(heartbeat)
        current_time = datetime.now(timezone.utc)
        return current_time - heartbeat_time > timedelta(seconds=self.idle_worker_delta)
