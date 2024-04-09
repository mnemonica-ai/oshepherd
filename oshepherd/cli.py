import click
from oshepherd.lib import load_and_validate_env
from oshepherd.api.app import start_flask_app
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
    """Starts the Flask API serving Ollama model's inference."""
    config: ApiConfig = load_and_validate_env(env_file, ApiConfig)

    start_flask_app(config)


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
    )
    worker.start()


if __name__ == "__main__":
    main()
