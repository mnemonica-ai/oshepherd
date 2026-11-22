"""Commands for named inference consumers."""

from pathlib import Path

import click


@click.group()
def client():
    """Manage named credentials for applications that consume inference."""


def options(function):
    function = click.option(
        "--state-dir",
        type=click.Path(path_type=Path),
        help="Installation state directory. Defaults to the share state directory.",
    )(function)
    return click.option(
        "--connection-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Private operator connection. Defaults to connection.json in the state directory.",
    )(function)


def run_client(connection_file, state_dir, action):
    from .named_clients import NamedClients
    from .onboarding import OnboardingError, default_state_dir
    from .operator_cli import run

    try:
        state_dir = state_dir or default_state_dir("share")
    except OnboardingError as error:
        raise click.ClickException(str(error)) from None
    if not (state_dir / "installation.json").exists():
        raise click.ClickException(
            "Installation state is missing. Select an existing installation with --state-dir."
        )
    connection_file = connection_file or state_dir / "connection.json"
    if not connection_file.exists():
        raise click.ClickException(
            "Private connection file is missing. Select it with --connection-file."
        )

    async def execute(operator):
        return await action(NamedClients(operator))

    run(connection_file, state_dir, execute)


@client.command()
@options
@click.option(
    "--name", required=True, help="Unique name for the consuming application."
)
@click.option(
    "--output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the private client JSON in an existing directory without replacing a file.",
)
def create(connection_file, state_dir, name, output):
    """Create a named key or export the existing key for that name."""
    run_client(connection_file, state_dir, lambda clients: clients.create(name, output))


@client.command("list")
@options
def list_clients(connection_file, state_dir):
    """List named clients recorded in this installation's local state."""

    async def execute(clients):
        return clients.list()

    run_client(connection_file, state_dir, execute)


@client.command()
@options
@click.option("--name", required=True, help="Name used when the client was created.")
def revoke(connection_file, state_dir, name):
    """Revoke the key belonging to a named client."""
    run_client(connection_file, state_dir, lambda clients: clients.revoke(name))
