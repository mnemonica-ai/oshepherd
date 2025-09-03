from celery import Task as CeleryTask
from redis.exceptions import ConnectionError as RedisConnectionError
from amqp.exceptions import RecoverableConnectionError as AMQPConnectionError
from httpx import ConnectError


class OllamaCeleryTask(CeleryTask):
    """
    Base Celery Task class for Ollama tasks.
    Handling error events related to connectivity issues in each worker:
        1. RedisConnectionError: `redis.exceptions.ConnectionError: Error while reading from 104 Connection reset by peer`
        2. AMQPConnectionError: `amqp.exceptions.RecoverableConnectionError: Socket was disconnected`
        3. ConnectionResetError: `ConnectionResetError: [Errno 104] Connection reset by peer`
        4. TimeoutError: `TimeoutError: [Errno 110] Connection timed out`
        5. ConnectError: `httpx.ConnectError: [Errno 61] Connection refused`
    """

    autoretry_for = (
        RedisConnectionError,
        AMQPConnectionError,
        ConnectionResetError,
        TimeoutError,
        ConnectError,
    )
    retry_kwargs = {  # Retry up to 5 times, 1 sec delay in between
        "max_retries": 5,
        "countdown": 1,
    }
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True

    def refresh_connections(self):
        """Refresh all worker connections when connection errors occur."""
        try:
            with self.app.connection() as connection:
                print(" > Refresh broker connection")
                connection.ensure_connection(max_retries=3)

            if hasattr(self.app.backend, "ensure_connection"):
                print(" > Refresh backend connection")
                self.app.backend.ensure_connection(max_retries=3)

            # Force connection pool refresh
            if hasattr(self.app.backend, "client"):
                self.app.backend.client.connection_pool.disconnect()
                print(" > Backend connection pool refreshed")

        except Exception as e:
            print(f" ! Connection refresh failed: {e}")
            print(" ! Worker connections may be in bad state - consider restart")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        print(f"Retrying task {self.name}:{task_id}, attempt {self.request.retries}")
        self.refresh_connections()

    def on_success(self, retval, task_id, args, kwargs):
        print(f"Completed task {self.name}:{task_id} successfully")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        print(f"Failed task {self.name}:{task_id} due to {exc} - {einfo}")

    def run(self, *args, **kwargs):
        raise NotImplementedError("Tasks must implement its run method")
