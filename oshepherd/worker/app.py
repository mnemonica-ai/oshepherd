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
        TASKS_MODULE, broker=config.RABBITMQ_URL, backend=config.REDIS_URL
    )
    celery_app.conf.update(
        result_expires=config.RESULTS_EXPIRES,
        broker_transport_options=config.BROKER_TRANSPORT_OPTIONS,
    )
    celery_app.autodiscover_tasks([TASKS_MODULE])

    return celery_app


def create_celery_app_for_flask(flask_app):
    config = WorkerConfig(
        RABBITMQ_URL=flask_app.config["RABBITMQ_URL"],
        REDIS_URL=flask_app.config["REDIS_URL"],
        RESULTS_EXPIRES=flask_app.config.get(
            "RESULTS_EXPIRES", WorkerConfig.__fields__["RESULTS_EXPIRES"].default
        ),
    )

    celery_app = create_celery_app(config)

    return celery_app
