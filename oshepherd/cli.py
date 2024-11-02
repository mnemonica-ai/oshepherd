import signal
import click
from oshepherd.api.gunicorn_app import GunicornApp
from oshepherd.lib import load_and_validate_env
from oshepherd.api.app import start_api_app
from oshepherd.api.config import ApiConfig
from oshepherd.worker.config import WorkerConfig
from oshepherd.worker.app import create_celery_app


@click.group()
def main():
    pass


@main.command()
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to the .env file with environment variables.",
)
def start_api(env_file):
    """Starts FastAPI serving Ollama model's inference."""
    config: ApiConfig = load_and_validate_env(env_file, ApiConfig)

    app = start_api_app(config)

    gunicorn_bind = f"{config.HOST}:{config.PORT}"
    options = {
        "bind": gunicorn_bind,
        "workers": 2,
        "worker_class": "uvicorn.workers.UvicornWorker",
    }
    gunicorn_app = GunicornApp(app, options)

    def graceful_shutdown(signum, frame):
        print("Shutting down gracefully...")
        gunicorn_app.stop()

    # Register signal handler for CTRL+C
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # Run the Gunicorn server
    try:
        gunicorn_app.run()
    except KeyboardInterrupt:
        print("Interrupted. Exiting...")


@main.command()
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to the .env file with environment variables.",
)
def start_worker(env_file):
    """Starts the Celery Worker serving local Ollama models."""
    config: WorkerConfig = load_and_validate_env(env_file, WorkerConfig)

    celery_app = create_celery_app(config)
    worker = celery_app.Worker(
        loglevel=config.LOGLEVEL,
        concurrency=config.CONCURRENCY,
        prefetch_multiplier=config.PREFETCH_MULTIPLIER,
        redis_retry_on_timeout=config.REDIS_RETRY_ON_TIMEOUT,
        redis_socket_keepalive=config.REDIS_SOCKET_KEEPALIVE,
    )
    worker.start()


if __name__ == "__main__":
    main()
