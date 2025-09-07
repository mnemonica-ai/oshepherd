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
        result_expires=config.RESULTS_EXPIRES,
        broker_transport_options=config.BROKER_TRANSPORT_OPTIONS,
        result_backend_transport_options=config.BROKER_TRANSPORT_OPTIONS,
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=10,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        broker_pool_limit=5,
        worker_prefetch_multiplier=config.PREFETCH_MULTIPLIER,
        worker_max_memory_per_child=200000,
        broker_heartbeat=30,
        broker_heartbeat_checkrate=3.0,
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
                time.sleep(30)

                # Test broker connection
                with app.connection() as connection:
                    connection.ensure_connection(max_retries=1)

                # Test backend connection with improved error handling
                redis_client = redis.Redis.from_url(
                    config.CELERY_BACKEND_URL,
                    socket_timeout=10,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                    max_connections=3,
                )
                redis_client.ping()
                redis_client.close()

                consecutive_failures = 0

            except Exception as e:
                consecutive_failures += 1
                print(
                    f" ! Connection monitor detected failure #{consecutive_failures}: {e}"
                )

                if consecutive_failures >= 3:
                    print(
                        " > Multiple failures detected, attempting connection refresh..."
                    )
                    try:
                        refresh_all_connections(app, config)
                        print(" > Connection refresh successful")
                        consecutive_failures = 0
                    except Exception as refresh_error:
                        print(f" ! Connection refresh failed: {refresh_error}")
                        if consecutive_failures >= 5:
                            print(
                                " ! Too many consecutive failures - worker may need manual restart"
                            )

    monitor_thread = threading.Thread(target=monitor_connections, daemon=True)
    monitor_thread.start()
    print(" > Enhanced connection monitor started")


def create_celery_app_for_fastapi(config):
    config = WorkerConfig(
        CELERY_BROKER_URL=config.CELERY_BROKER_URL,
        CELERY_BACKEND_URL=config.CELERY_BACKEND_URL,
        RESULTS_EXPIRES=WorkerConfig.model_fields["RESULTS_EXPIRES"].default,
    )

    celery_app = create_celery_app(config)

    return celery_app
