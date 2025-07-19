from celery import Celery
from oshepherd.worker.config import WorkerConfig

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

    print(f" > Broker URL: {config.CELERY_BROKER_URL}")
    print(f" > Backend URL: {config.CELERY_BACKEND_URL}")
    print(f" > Prefetch Multiplier: {config.PREFETCH_MULTIPLIER}")

    # Configure Celery with simplified settings
    celery_app.conf.update(
        result_expires=config.RESULTS_EXPIRES,
        broker_transport_options=config.BROKER_TRANSPORT_OPTIONS,
        # Connection retry settings (addresses deprecation warnings)
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=5,
        # Task handling settings
        worker_prefetch_multiplier=config.PREFETCH_MULTIPLIER,
        task_acks_late=True,  # Acknowledge tasks only after completion
        task_reject_on_worker_lost=True,
        # Connection pool settings
        broker_pool_limit=10,
        broker_connection_timeout=30,
    )
    celery_app.autodiscover_tasks([TASKS_MODULE])

    # Debug: Show registered tasks
    print(f" > Registered tasks: {list(celery_app.tasks.keys())}")

    return celery_app


def create_celery_app_for_fastapi(config):
    config = WorkerConfig(
        CELERY_BROKER_URL=config.CELERY_BROKER_URL,
        CELERY_BACKEND_URL=config.CELERY_BACKEND_URL,
        RESULTS_EXPIRES=WorkerConfig.model_fields["RESULTS_EXPIRES"].default,
    )

    celery_app = create_celery_app(config)

    return celery_app
