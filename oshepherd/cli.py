import click
import asyncio
from oshepherd.common.lib import load_and_validate_env
from oshepherd.api.config import ApiConfig
from oshepherd.worker.config import WorkerConfig
from oshepherd.common.logging_config import configure_logging


@click.group()
def main():
    pass


@main.command()
@click.option(
    "--backend",
    type=click.Choice(["redis", "supabase"]),
    envvar="OSHEPHERD_BACKEND",
    default="redis",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to the .env file with environment variables.",
)
def start_api(env_file, backend):
    """Starts FastAPI serving Ollama model's inference."""
    from oshepherd.api.factory import create_api_app
    from oshepherd.api.server import setup_api_server

    config_type = ApiConfig
    if backend == "supabase":
        from oshepherd.backends.supabase.config import SupabaseGatewayConfig

        config_type = SupabaseGatewayConfig
    config = load_and_validate_env(config_type, env_file)
    if config is None:
        raise click.ClickException("Invalid API configuration")

    configure_logging("api", config.LOGLEVEL)

    app = create_api_app(config, backend=backend)
    server = setup_api_server(app, config)
    server.run()


@main.command()
@click.option(
    "--backend",
    type=click.Choice(["redis", "supabase"]),
    envvar="OSHEPHERD_BACKEND",
    default="redis",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Path to the .env file with environment variables.",
)
def start_worker(env_file, backend):
    """Starts a worker serving local Ollama models through the selected backend."""
    if backend == "supabase":
        from oshepherd.backends.supabase.config import SupabaseWorkerConfig
        from oshepherd.backends.supabase.startup import (
            start_worker as run_supabase_worker,
        )
        from oshepherd.backends.supabase.client import RpcError

        config = load_and_validate_env(SupabaseWorkerConfig, env_file)
        if config is None:
            raise click.ClickException("Invalid Supabase worker configuration")
        configure_logging("worker", config.LOGLEVEL)
        try:
            asyncio.run(run_supabase_worker(config))
        except RpcError as error:
            raise click.ClickException(error.code) from None
        except (OSError, ValueError):
            raise click.ClickException(
                "Worker startup failed; check configuration and private session file"
            ) from None
        return
    from oshepherd.worker.worker_data import WorkerData
    from oshepherd.worker.app import create_celery_app

    config: WorkerConfig = load_and_validate_env(WorkerConfig, env_file)
    if config is None:
        raise click.ClickException("Invalid worker configuration")

    configure_logging("worker", config.LOGLEVEL)

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


@main.command()
@click.option("--env-file", type=click.Path(exists=True))
@click.option(
    "--enrollment-file",
    type=click.Path(exists=True),
    help="Owned 0600 file containing a one-time enrollment token.",
)
def supabase_worker_login(env_file, enrollment_file):
    """Exchange an operator-issued code and save a private worker session."""
    from oshepherd.backends.supabase.config import SupabaseWorkerConfig
    from oshepherd.backends.supabase.startup import login_worker, read_enrollment
    from oshepherd.backends.supabase.client import RpcError

    config = load_and_validate_env(SupabaseWorkerConfig, env_file)
    if config is None:
        raise click.ClickException("Invalid Supabase worker configuration")
    if config.WORKER_SESSION_FILE.exists():
        raise click.ClickException(
            "Worker session already exists; use start-worker to refresh it"
        )
    try:
        token = (
            read_enrollment(enrollment_file, supabase_url=config.SUPABASE_URL)
            if enrollment_file
            else click.prompt("One-time enrollment code", hide_input=True)
        )
        asyncio.run(login_worker(config, token))
    except RpcError as error:
        raise click.ClickException(error.code) from None
    except (OSError, ValueError):
        raise click.ClickException(
            "Enrollment failed; check the private enrollment/session files"
        ) from None
    click.echo(
        "Worker session saved privately; ready for start-worker --backend supabase"
    )


@main.command()
@click.option("--env-file", type=click.Path(exists=True))
@click.option("--once", is_flag=True, help="Run one bounded maintenance cycle.")
def supabase_maintain(env_file, once):
    """Run operator cleanup independently of workers and gateway requests."""
    from oshepherd.backends.supabase.config import SupabaseMaintenanceConfig
    from oshepherd.backends.supabase.maintenance import run_maintenance
    from oshepherd.backends.supabase.client import RpcError
    import json

    config = load_and_validate_env(SupabaseMaintenanceConfig, env_file)
    if config is None:
        raise click.ClickException("Invalid Supabase maintenance configuration")
    configure_logging("maintenance", config.LOGLEVEL)
    try:
        result = asyncio.run(run_maintenance(config, once=once))
    except RpcError as error:
        raise click.ClickException(error.code) from None
    except (OSError, ValueError, TimeoutError):
        raise click.ClickException(
            "Maintenance failed; check operator configuration"
        ) from None
    if once:
        click.echo(json.dumps(result))


from oshepherd.backends.supabase.operator_cli import supabase


class _OnboardingCommand(click.Command):
    """Keep optional Supabase/POSIX dependencies out of existing Redis commands."""

    def __init__(self, name, module, help):
        super().__init__(name, help=help)
        self.module = module

    def _load(self):
        from importlib import import_module

        return getattr(import_module(self.module), self.name)

    def make_context(self, info_name, args, parent=None, **extra):
        return self._load().make_context(info_name, args, parent=parent, **extra)

    def invoke(self, ctx):
        return ctx.command.invoke(ctx)

    def shell_complete(self, ctx, incomplete):
        return self._load().shell_complete(ctx, incomplete)


main.add_command(supabase)
for _name, _module, _help in (
    (
        "share",
        "share_cli",
        "Share local Ollama through an experimental Supabase installation.",
    ),
    (
        "join",
        "join_cli",
        "Join an installation or restart its saved foreground worker.",
    ),
    ("invite", "share_cli", "Create a private invitation for another machine."),
):
    main.add_command(
        _OnboardingCommand(_name, "oshepherd.backends.supabase." + _module, _help)
    )

if __name__ == "__main__":
    main()
