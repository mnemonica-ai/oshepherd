import json
import ollama
import socket
import threading
import time
import uuid
import requests
from datetime import datetime, timezone
from oshepherd.worker.config import WorkerConfig
from oshepherd.common.redis_service import RedisService

OSHEPHERD_WORKER_HOSTNAME = socket.gethostname()
OSHEPHERD_WORKER_UUID = uuid.uuid4().hex
OSHEPHERD_WORKER_DATA_PUSH_INTERVAL = 10  # secs
OSHEPHERD_WORKERS_PREFIX_KEY = "oshepherd_worker:"


class WorkerData:

    def __init__(self, config: WorkerConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.config = config
        self.ollama_base_url = config.OLLAMA_BASE_URL
        self.redis_service = RedisService(self.backend_url)
        self.hostname = OSHEPHERD_WORKER_HOSTNAME
        self.worker_uuid = OSHEPHERD_WORKER_UUID
        self.worker_id = f"{self.hostname}-{self.worker_uuid}"

    def get_ollama_version(self):
        res = {}
        try:
            req_res = requests.get(f"{self.ollama_base_url}/api/version")
            req_res.raise_for_status()
            res = req_res.json()
        except Exception as error:
            res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama version fn: {res}",
            )
        return res

    def get_ollama_list(self):
        res = {}
        try:
            ollama_response = ollama.list()
            # Convert ListResponse to dict for JSON serialization
            res = (
                ollama_response.model_dump()
                if hasattr(ollama_response, "model_dump")
                else dict(ollama_response)
            )
        except Exception as error:
            res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama list fn: {res}",
            )
        return res

    def get_ollama_ps(self):
        res = {}
        try:
            ollama_response = ollama.ps()
            # Convert ProcessResponse to dict for JSON serialization
            res = (
                ollama_response.model_dump()
                if hasattr(ollama_response, "model_dump")
                else dict(ollama_response)
            )
        except Exception as error:
            res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama ps fn: {res}",
            )
        return res

    def get_ollama_show(self, model_name):
        """Get show information for a specific model."""
        res = {}
        try:
            ollama_response = ollama.show(model_name)
            # Convert ShowResponse to dict for JSON serialization
            res = (
                ollama_response.model_dump()
                if hasattr(ollama_response, "model_dump")
                else dict(ollama_response)
            )
        except Exception as error:
            res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama show fn for model {model_name}: {res}",
            )
        return res

    def get_ollama_show_map(self):
        """Get show information for all available models."""
        show_map = {}
        try:
            list_res = self.get_ollama_list()
            models = list_res.get("models", [])

            for model in models:
                model_name = model.get("model")
                if model_name:
                    show_res = self.get_ollama_show(model_name)
                    show_map[model_name] = show_res

            print(
                f" >>> worker {self.worker_id} fetched show data for {len(show_map)} models"
            )
        except Exception as e:
            print(f" ! Failed to fetch show data: {e}")

        return show_map

    def get_data(self):
        version_res = self.get_ollama_version()
        serialized_version_res = json.dumps(version_res, default=str)
        list_res = self.get_ollama_list()
        serialized_list_res = json.dumps(list_res, default=str)
        ps_res = self.get_ollama_ps()
        serialized_ps_res = json.dumps(ps_res, default=str)
        show_map = self.get_ollama_show_map()
        serialized_show_map = json.dumps(show_map, default=str)
        now = datetime.now(timezone.utc).isoformat()

        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "uuid": self.worker_uuid,
            "version": serialized_version_res,
            "tags": serialized_list_res,
            "ps": serialized_ps_res,
            "show": serialized_show_map,
            "heartbeat": now,
        }

    def push_data(self):
        try:
            worker_data = self.get_data()
            self.redis_service.hset(
                f"{OSHEPHERD_WORKERS_PREFIX_KEY}{self.worker_id}", mapping=worker_data
            )
            print(f" >>> worker {self.worker_id} data pushed")
        except Exception as e:
            print(f" ! Data push failed: {e}")

    def start_data_push(self):
        print(f" > worker {self.worker_id} data push setup starting")

        def run_periodically():
            while True:
                self.push_data()
                time.sleep(OSHEPHERD_WORKER_DATA_PUSH_INTERVAL)

        thread = threading.Thread(target=run_periodically)
        thread.daemon = True
        thread.start()

        print(f" > worker {self.worker_id} data push setup finished")
