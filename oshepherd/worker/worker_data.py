import json
import ollama
import socket
import threading
import time
import uuid
import requests
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from datetime import datetime, timezone
from oshepherd.worker.config import WorkerConfig
from oshepherd.common.redis import get_socket_keepalive_options

OSHEPHERD_WORKER_HOSTNAME = socket.gethostname()
OSHEPHERD_WORKER_UUID = uuid.uuid4().hex
OSHEPHERD_WORKERS_PREFIX_KEY = "oshepherd_worker:"
OLLAMA_BASE_URL = "http://localhost:11434"  # TODO get from env var


class WorkerData:

    def __init__(self, config: WorkerConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.hostname = OSHEPHERD_WORKER_HOSTNAME
        self.worker_uuid = OSHEPHERD_WORKER_UUID
        self.worker_id = f"{self.hostname}-{self.worker_uuid}"
        self.heartbeat_interval = 10  # seconds

        print(f" > Worker data using standard Redis connection")
        self.redis_client = Redis.from_url(
            self.backend_url,
            socket_keepalive=True,
            socket_keepalive_options=get_socket_keepalive_options(),
            retry_on_timeout=True,
            max_connections=10,
        )

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
        """Push worker data to Redis."""
        worker_data = self.get_data()

        try:
            self.redis_client.hset(
                f"{OSHEPHERD_WORKERS_PREFIX_KEY}{self.worker_id}",
                mapping=worker_data,
            )
            print(f" >>> worker {self.worker_id} data pushed")
        except Exception as e:
            print(f" >>> Failed to push worker data: {e}")
            # Let the health check handle connection issues

    def start_data_push(self):
        print(f" > worker {self.worker_id} data push setup starting")
        print(f" > heartbeat interval: {self.heartbeat_interval} seconds")

        def run_periodically():
            while True:
                try:
                    self.push_data()
                    time.sleep(self.heartbeat_interval)
                except Exception as e:
                    print(f" >>> Heartbeat thread error: {e}")
                    print(
                        f" >>> Heartbeat thread will continue after {self.heartbeat_interval} seconds"
                    )
                    time.sleep(self.heartbeat_interval)

        def start_heartbeat_thread():
            thread = threading.Thread(target=run_periodically, daemon=True)
            thread.start()
            return thread

        heartbeat_thread = start_heartbeat_thread()

        def monitor_heartbeat():
            nonlocal heartbeat_thread
            while True:
                time.sleep(60)
                if not heartbeat_thread.is_alive():
                    print(f" >>> Heartbeat thread died, restarting...")
                    heartbeat_thread = start_heartbeat_thread()

        monitor_thread = threading.Thread(target=monitor_heartbeat, daemon=True)
        monitor_thread.start()

        print(f" > worker {self.worker_id} data push setup finished")
