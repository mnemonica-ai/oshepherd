import click
from oshepherd.lib import load_and_validate_env
from oshepherd.api.app import setup_api_app
from oshepherd.api.server import setup_api_server
from oshepherd.api.config import ApiConfig
from oshepherd.worker.config import WorkerConfig
from oshepherd.worker.worker_data import WorkerData
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

    app = setup_api_app(config)
    server = setup_api_server(app, config)
    server.run()


@main.command()
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to the .env file with environment variables.",
)
def start_worker(env_file):
    """Starts the Celery Worker serving local Ollama models."""
    config: WorkerConfig = load_and_validate_env(env_file, WorkerConfig)

    worker_data = WorkerData(config)
    worker_data.start_data_push()

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
