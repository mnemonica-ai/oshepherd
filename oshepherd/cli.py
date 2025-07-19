import sys
import threading
import time
import click
from oshepherd.common.utils import load_and_validate_env
from oshepherd.api.app import setup_api_app
from oshepherd.api.server import setup_api_server
from oshepherd.api.config import ApiConfig
from oshepherd.worker.config import WorkerConfig
from oshepherd.worker.worker_data import WorkerData
from oshepherd.worker.app import create_celery_app
from oshepherd.common.celery import check_worker_health


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
    """Starts the Celery Worker serving local Ollama models with auto-restart on connection issues."""
    config: WorkerConfig = load_and_validate_env(env_file, WorkerConfig)
    restart_count = 0

    while restart_count < config.MAX_RESTART_ATTEMPTS:
        try:
            if restart_count > 0:
                print(
                    f" > Restarting worker (attempt {restart_count + 1}/{config.MAX_RESTART_ATTEMPTS})"
                )
                time.sleep(5)  # Wait 5 seconds before restart

            worker_data = WorkerData(config)
            worker_data.start_data_push()

            celery_app = create_celery_app(config)

            # Start health monitoring in the background
            health_thread = threading.Thread(
                target=check_worker_health,
                args=(celery_app, config.HEALTH_CHECK_INTERVAL),
                daemon=True,
            )
            health_thread.start()
            print(
                f" > Started worker health monitoring (interval: {config.HEALTH_CHECK_INTERVAL}s)"
            )

            # Start the worker
            worker = celery_app.Worker(
                loglevel=config.LOGLEVEL,
                concurrency=config.CONCURRENCY,
                prefetch_multiplier=config.PREFETCH_MULTIPLIER,
                redis_retry_on_timeout=config.REDIS_RETRY_ON_TIMEOUT,
                redis_socket_keepalive=config.REDIS_SOCKET_KEEPALIVE,
            )
            worker.start()

            print(" > Worker stopped listening")
            break

        except KeyboardInterrupt:
            print(" > Worker interrupted by user")
            break
        except SystemExit as e:
            # Health check failure
            if e.code == 1:
                restart_count += 1
                print(
                    f" >>> Connection health check failed, restart attempt {restart_count}/{config.MAX_RESTART_ATTEMPTS}"
                )
                if restart_count >= config.MAX_RESTART_ATTEMPTS:
                    print(
                        f" >>> Maximum restart attempts ({config.MAX_RESTART_ATTEMPTS}) reached, giving up"
                    )
                    sys.exit(1)
            else:
                print(f" > Worker stopped because: {e}")
                break
        except Exception as e:
            # Worker general failure
            restart_count += 1
            print(f" >>> Worker crashed with error: {e}")
            print(f" >>> Restart attempt {restart_count}/{config.MAX_RESTART_ATTEMPTS}")
            if restart_count >= config.MAX_RESTART_ATTEMPTS:
                print(
                    f" >>> Maximum restart attempts ({config.MAX_RESTART_ATTEMPTS}) reached, giving up"
                )
                sys.exit(1)

    print(" > Worker process finished")


if __name__ == "__main__":
    main()
