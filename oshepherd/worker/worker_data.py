import json
import ollama
import socket
import threading
import time
import uuid
from redis import Redis
from datetime import datetime, timezone
from oshepherd.worker.config import WorkerConfig

OSHEPHERD_WORKER_HOSTNAME = socket.gethostname()
OSHEPHERD_WORKER_UUID = uuid.uuid4().hex
OSHEPHERD_WORKER_DATA_PUSH_INTERVAL = 5  # secs
OSHEPHERD_WORKERS_PREFIX_KEY = "oshepherd_worker:"


class WorkerData:

    def __init__(self, config: WorkerConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.redis_client = Redis.from_url(self.backend_url)
        self.hostname = OSHEPHERD_WORKER_HOSTNAME
        self.worker_uuid = OSHEPHERD_WORKER_UUID
        self.worker_id = f"{self.hostname}-{self.worker_uuid}"

    def get_data(self):
        list_res = None
        try:
            list_res = ollama.list()
        except Exception as error:
            list_res = {
                "error": {"type": str(error.__class__.__name__), "message": str(error)}
            }
            print(
                f" *** error ollama list fn: {list_res}",
            )

        serialized_list_res = json.dumps(list_res)
        now = datetime.now(timezone.utc).isoformat()

        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "uuid": self.worker_uuid,
            "tags": serialized_list_res,
            "heartbeat": now,
        }

    def push_data(self):
        worker_data = self.get_data()
        self.redis_client.hset(
            f"{OSHEPHERD_WORKERS_PREFIX_KEY}{self.worker_id}", mapping=worker_data
        )
        print(f" >>> worker {self.worker_id} data pushed")

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
