from celery import Celery
from celery.signals import worker_ready, worker_process_init
from oshepherd.worker.config import WorkerConfig
import redis
import time
import threading

TASKS_MODULE = "oshepherd.worker.tasks"

celery_app = None


def create_celery_app(config: WorkerConfig):
    """
    Create a Celery app instance given a certain configuration.
    Refs:
        * https://docs.celeryq.dev/en/stable/getting-started/next-steps.html#using-celery-in-your-application
        * https://docs.celeryq.dev/en/stable/getting-started/first-steps-with-celery.html#celerytut-keeping-results
    """
    global celery_app

    celery_app = Celery(
        TASKS_MODULE, broker=config.CELERY_BROKER_URL, backend=config.CELERY_BACKEND_URL
    )
    celery_app.conf.update(
        result_expires=1800,  # 30 minutes
        broker_transport_options=config.BROKER_TRANSPORT_OPTIONS,
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=10,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        broker_pool_limit=2,
        worker_prefetch_multiplier=config.PREFETCH_MULTIPLIER,
        worker_max_memory_per_child=200000,
        broker_heartbeat=30,
        broker_heartbeat_checkrate=3.0,
        # Celery 5.5+ optimizations
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_disable_rate_limits=True,
        # Disable compression (having in mind smallest 30MB memory limit)
        task_compression=None,
        result_compression=None,
        result_accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Redis-specific optimizations
        result_backend_transport_options={
            "retry_policy": {"timeout": 5.0},
            "socket_keepalive": True,
            "socket_keepalive_options": {},
        },
    )
    celery_app.autodiscover_tasks([TASKS_MODULE])

    setup_connection_monitoring(celery_app, config)

    return celery_app


def setup_connection_monitoring(app, config: WorkerConfig):
    """Setup connection monitoring and worker refresh on connection loss."""

    @worker_ready.connect
    def worker_ready_handler(sender=None, **kwargs):
        print(" > Worker ready - starting connection monitoring")
        start_connection_monitor(app, config)

    @worker_process_init.connect
    def worker_process_init_handler(sender=None, **kwargs):
        print(" > Worker process initialized - refreshing connections")
        refresh_all_connections(app, config)


def refresh_all_connections(app, config: WorkerConfig):
    """Refresh all Redis connections for the worker."""
    try:
        with app.connection() as connection:
            connection.ensure_connection(max_retries=3)
            print(" > Broker connection refreshed")

        if hasattr(app.backend, "ensure_connection"):
            app.backend.ensure_connection(max_retries=3)
            print(" > Backend connection refreshed")

        # Test Redis connection directly
        redis_client = redis.Redis.from_url(config.CELERY_BACKEND_URL)
        redis_client.ping()
        redis_client.close()
        print(" > Direct Redis connection verified")

    except Exception as e:
        print(f" ! Connection refresh failed: {e}")
        raise


def start_connection_monitor(app, config: WorkerConfig):
    """Start background thread to monitor connections and refresh worker if needed."""

    def monitor_connections():
        consecutive_failures = 0
        while True:
            try:
                # Shorter sleep interval to catch idle timeouts faster
                time.sleep(180)  # 3 minutes

                # Test broker connection
                with app.connection() as connection:
                    connection.ensure_connection(max_retries=1)

                # Test backend connection
                redis_client = redis.Redis.from_url(
                    config.CELERY_BACKEND_URL,
                    socket_timeout=5,
                    socket_connect_timeout=3,
                    retry_on_timeout=True,
                    max_connections=2,
                    socket_keepalive=True,
                    socket_keepalive_options={},
                )
                redis_client.ping()
                redis_client.close()

                consecutive_failures = 0

            except Exception as e:
                consecutive_failures += 1
                print(
                    f" ! Connection monitor detected failure #{consecutive_failures}: {e}"
                )

                # More aggressive recovery
                if consecutive_failures >= 2:
                    print(
                        " > Connection issues detected, attempting immediate refresh..."
                    )
                    try:
                        refresh_all_connections(app, config)
                        print(" > Connection refresh successful")
                        consecutive_failures = 0
                    except Exception as refresh_error:
                        print(f" ! Connection refresh failed: {refresh_error}")
                        if consecutive_failures >= 4:
                            print(
                                " ! Multiple refresh failures - consider restarting worker"
                            )

    monitor_thread = threading.Thread(target=monitor_connections, daemon=True)
    monitor_thread.start()
    print(" > Connection monitor started")


def create_celery_app_for_fastapi(config):
    config = WorkerConfig(
        CELERY_BROKER_URL=config.CELERY_BROKER_URL,
        CELERY_BACKEND_URL=config.CELERY_BACKEND_URL,
        RESULTS_EXPIRES=WorkerConfig.model_fields["RESULTS_EXPIRES"].default,
    )

    celery_app = create_celery_app(config)

    return celery_app
