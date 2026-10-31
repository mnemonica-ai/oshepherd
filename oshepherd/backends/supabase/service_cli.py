"""Explicit management of per-user services; never called by share implicitly."""

import json
from pathlib import Path

import click


def _execute(name, action, **kwargs):
    from .service import ServiceError, UserService
    from .releases import OperatorError

    try:
        result = getattr(UserService(name), action)(**kwargs)
    except (ServiceError, OperatorError) as error:
        raise click.ClickException(str(error)) from None
    except Exception:
        raise click.ClickException(
            "Service operation failed; check private state and the user service manager"
        ) from None
    click.echo(json.dumps(result))


@click.group()
def service():
    """Manage optional per-user background sharing after foreground onboarding.

    Services start at user login. Uninstall preserves cloud resources, credentials,
    worker state and the owned Python runtime. No administrator privileges needed.
    """


@service.command("install")
@click.option("--name", default="default", show_default=True)
@click.option("--kind", type=click.Choice(["share", "join"]), required=True)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path, file_okay=False, exists=True),
    required=True,
)
@click.option(
    "--connection-file",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="Existing share operator connection; defaults to state-dir/connection.json.",
)
@click.option(
    "--python",
    "executable",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="Advanced: durable Python with this installed package. By default an owned uv runtime is prepared if needed.",
)
def install(name, kind, state_dir, connection_file, executable):
    """Install and start this already verified share or joined worker."""
    _execute(
        name,
        "install",
        kind=kind,
        state_dir=state_dir,
        connection_file=connection_file,
        executable=executable,
    )


for _action in ("start", "stop", "status", "uninstall"):

    def _command(name, action=_action):
        _execute(name, action)

    service.add_command(
        click.command(_action)(
            click.option("--name", default="default", show_default=True)(_command)
        )
    )
