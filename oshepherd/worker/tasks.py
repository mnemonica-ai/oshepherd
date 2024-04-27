import json
from celery import Task as CeleryTask
from redis.exceptions import ConnectionError as RedisConnectionError
from amqp.exceptions import RecoverableConnectionError as AMQPConnectionError
from httpx import ConnectError
import ollama
from oshepherd.worker.app import celery_app


class OllamaTask(CeleryTask):
    """
    Base Celery Task class for ollama tasks.
    Handling error events related to ollama server in each worker:
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
    retry_kwargs = {
        "max_retries": 5,
        "countdown": 1,
    }  # Retry up to 5 times, with a 1 second delay between retries
    retry_backoff = True  # Enable exponential backoff
    retry_backoff_max = 60  # Maximum retry delay in seconds
    retry_jitter = True  # Add a random jitter to delay to prevent all tasks from retrying at the same time

    def refresh_connections(self):
        with self.app.connection() as connection:
            print(" > Refresh broker connection")
            connection.ensure_connection(max_retries=3)

        if hasattr(self.app.backend, "ensure_connection"):
            print(" > Refresh backend connection")
            self.app.backend.ensure_connection(max_retries=3)

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        # Explicitly close and refresh connections if possible
        print(f"Retrying task {self.name}:{task_id}, attempt {self.request.retries}")
        self.refresh_connections()

    def on_success(self, retval, task_id, args, kwargs):
        print(f"Completed task {self.name}:{task_id} successfully")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        print(f"Failed task {self.name}:{task_id} due to {exc} - {einfo}")

    def run(self, *args, **kwargs):
        raise NotImplementedError("Tasks must implement its run method")


@celery_app.task(
    name="oshepherd.worker.tasks.make_generate_request", bind=True, base=OllamaTask
)
def make_generate_request(self, request_str: str):
    try:
        request = json.loads(request_str)
        print(f"# make_generate_request request {request}")

        response = ollama.generate(**request)
        print(f"  $ success {response}")
    except Exception as error:
        print(f"  * error make_generate_request {error}")
        response = {
            "error": {"type": str(error.__class__.__name__), "message": str(error)}
        }
        print(
            f"    * error response {response}",
        )

        # Rethrow exception in order to be handled by base class
        raise

    return response
