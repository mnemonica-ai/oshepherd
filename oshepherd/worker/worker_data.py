import json
import ollama
import socket
import threading
import time
import uuid
import requests
from datetime import datetime, timezone
from oshepherd.worker.config import WorkerConfig
from oshepherd.redis_service import RedisService

OSHEPHERD_WORKER_HOSTNAME = socket.gethostname()
OSHEPHERD_WORKER_UUID = uuid.uuid4().hex
OSHEPHERD_WORKER_DATA_PUSH_INTERVAL = 10  # secs
OSHEPHERD_WORKERS_PREFIX_KEY = "oshepherd_worker:"
OLLAMA_BASE_URL = "http://localhost:11434"  # TODO get from env var


class WorkerData:

    def __init__(self, config: WorkerConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.config = config
        self.redis_service = RedisService(self.backend_url)
        self.hostname = OSHEPHERD_WORKER_HOSTNAME
        self.worker_uuid = OSHEPHERD_WORKER_UUID
        self.worker_id = f"{self.hostname}-{self.worker_uuid}"

    def get_ollama_version(self):
        res = {}
        try:
            req_res = requests.get(f"{OLLAMA_BASE_URL}/api/version")
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
            res = ollama.list()
        except Exception as error:
            res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama list fn: {res}",
            )
        return res

    def get_data(self):
        version_res = self.get_ollama_version()
        serialized_version_res = json.dumps(version_res)
        list_res = self.get_ollama_list()
        serialized_list_res = json.dumps(list_res)
        now = datetime.now(timezone.utc).isoformat()

        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "uuid": self.worker_uuid,
            "version": serialized_version_res,
            "tags": serialized_list_res,
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
