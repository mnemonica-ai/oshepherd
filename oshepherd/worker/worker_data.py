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

OSHEPHERD_WORKER_HOSTNAME = socket.gethostname()
OSHEPHERD_WORKER_UUID = uuid.uuid4().hex
OSHEPHERD_WORKERS_PREFIX_KEY = "oshepherd_worker:"
OLLAMA_BASE_URL = "http://localhost:11434"  # TODO get from env var


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


class WorkerData:

    def __init__(self, config: WorkerConfig):
        self.backend_url = config.CELERY_BACKEND_URL
        self.hostname = OSHEPHERD_WORKER_HOSTNAME
        self.worker_uuid = OSHEPHERD_WORKER_UUID
        self.worker_id = f"{self.hostname}-{self.worker_uuid}"
        self.connection_recycling = config.REDIS_CONNECTION_RECYCLING
        self.heartbeat_interval = config.REDIS_HEARTBEAT_INTERVAL

        if self.connection_recycling:
            print(f" > Worker data using connection recycling mode")
            self.redis_client = None
        else:
            print(f" > Worker data using persistent connection mode")
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
        """
        Push worker data to Redis.
        - Recycling mode: create fresh connection for each push
        - Persistent mode: use retry logic
        """

        worker_data = self.get_data()

        if self.connection_recycling:
            redis_client = self._get_fresh_redis_client()
            try:
                redis_client.hset(
                    f"{OSHEPHERD_WORKERS_PREFIX_KEY}{self.worker_id}",
                    mapping=worker_data,
                )
                print(f" >>> worker {self.worker_id} data pushed (recycling mode)")
            except RedisConnectionError as e:
                print(f" >>> Redis connection error (recycling mode): {e}")
            finally:
                redis_client.close()
        else:
            def _push_operation():
                return self.redis_client.hset(
                    f"{OSHEPHERD_WORKERS_PREFIX_KEY}{self.worker_id}",
                    mapping=worker_data,
                )

            try:
                self._execute_with_retry(_push_operation)
                print(f" >>> worker {self.worker_id} data pushed (persistent mode)")
            except Exception as e:
                print(f" >>> Failed to push worker data: {e}")

    def start_data_push(self):
        print(f" > worker {self.worker_id} data push setup starting")
        print(f" > heartbeat interval: {self.heartbeat_interval} seconds")
        print(f" > connection recycling: {self.connection_recycling}")

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
