from celery import Task as CeleryTask
from redis.exceptions import ConnectionError as RedisConnectionError
from amqp.exceptions import RecoverableConnectionError as AMQPConnectionError
from httpx import ConnectError
import logging

logger = logging.getLogger(__name__)


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
                logger.info("refresh broker connection")
                connection.ensure_connection(max_retries=3)

            if hasattr(self.app.backend, "ensure_connection"):
                logger.info("refresh backend connection")
                self.app.backend.ensure_connection(max_retries=3)

            # Force connection pool refresh
            if hasattr(self.app.backend, "client"):
                self.app.backend.client.connection_pool.disconnect()
                logger.info("backend connection pool refreshed")

        except Exception as e:
            logger.exception("connection refresh failed error=%s", e)
            logger.error("worker connections may be in bad state - consider restart")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        logger.warning(
            "retrying task name=%s task_id=%s attempt=%s error=%s",
            self.name,
            task_id,
            self.request.retries,
            exc,
        )
        self.refresh_connections()

    def on_success(self, retval, task_id, args, kwargs):
        logger.info("completed task name=%s task_id=%s", self.name, task_id)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            "failed task name=%s task_id=%s error=%s traceback=%s",
            self.name,
            task_id,
            exc,
            einfo,
        )

    def run(self, *args, **kwargs):
        raise NotImplementedError("Tasks must implement its run method")
