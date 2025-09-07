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
        while True:
            try:
                time.sleep(60)

                # Test broker connection
                with app.connection() as connection:
                    connection.ensure_connection(max_retries=1)

                # Test backend connection
                redis_client = redis.Redis.from_url(config.CELERY_BACKEND_URL)
                redis_client.ping()
                redis_client.close()

            except Exception as e:
                print(f" ! Connection monitor detected failure: {e}")
                print(" > Refreshing all connections...")
                try:
                    refresh_all_connections(app, config)
                    print(" > Connection refresh successful")
                except Exception as refresh_error:
                    print(f" ! Connection refresh failed: {refresh_error}")
                    print(" ! Worker may need manual restart")

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
