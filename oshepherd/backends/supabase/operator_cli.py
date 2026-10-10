"""Lazy CLI entry points for the Supabase operator lifecycle."""

import asyncio
import json
from pathlib import Path

import click


@click.group("supabase")
def supabase():
    """Install, inspect and administer a Supabase v1 candidate."""


@supabase.command()
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path(".oshepherd/operator/connection.json"),
    show_default=True,
)
@click.option(
    "--local-from",
    type=click.Path(exists=True, path_type=Path),
    help="Import the isolated local helper's owned 0600 connection file.",
)
def configure(output, local_from):
    """Create private connection JSON using hidden prompts; refuse overwrites."""
    from .operator import OperatorConfig
    from .private_files import read_private_json, write_private_json

    if output.exists():
        raise click.ClickException(
            "Connection file already exists; edit it privately to change configuration"
        )
    try:
        if local_from:
            local = read_private_json(local_from)
            values = {
                "project_ref": "local",
                "supabase_url": local["API_URL"],
                "publishable_key": local["PUBLISHABLE_KEY"],
                "secret_key": local["SECRET_KEY"],
                "database_url": "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
                "profile": "edge_free",
            }
        else:
            ref = click.prompt("Selected Supabase project reference")
            values = {
                "project_ref": ref,
                "supabase_url": "https://" + ref + ".supabase.co",
                "publishable_key": click.prompt("Publishable key", hide_input=True),
                "secret_key": click.prompt("Server-only secret key", hide_input=True),
                "database_url": click.prompt(
                    "Postgres connection URL", hide_input=True
                ),
                "profile": click.prompt(
                    "Free hosting profile",
                    type=click.Choice(["edge_free", "vercel_hobby"]),
                    default="edge_free",
                ),
                "exposed_schemas": [
                    name.strip()
                    for name in click.prompt(
                        "Existing exposed schemas from project API settings (comma separated)"
                    ).split(",")
                ],
            }
            if values["profile"] == "vercel_hobby":
                values["gateway_url"] = (
                    click.prompt(
                        "Deployed gateway HTTPS origin (blank until deployment)",
                        default="",
                        show_default=False,
                    )
                    or None
                )
        config = OperatorConfig.model_validate(values)
        data = config.model_dump(mode="json")
        for name in ("publishable_key", "secret_key", "database_url"):
            if getattr(config, name) is not None:
                data[name] = getattr(config, name).get_secret_value()
        write_private_json(output, data)
    except (OSError, ValueError, KeyError, TypeError):
        raise click.ClickException(
            "Invalid connection configuration; no credentials were printed"
        ) from None
    click.echo("Private connection saved; select an explicit Auth owner before install")


def options(function):
    function = click.option(
        "--state-dir",
        type=click.Path(path_type=Path),
        default=Path(".oshepherd/operator"),
        show_default=True,
    )(function)
    return click.option(
        "--connection-file",
        type=click.Path(exists=True, path_type=Path),
        required=True,
        help="Owned 0600 JSON; credentials are never CLI arguments.",
    )(function)


def run(connection_file, state_dir, action):
    from .client import RpcError
    from .operator import Operator, OperatorConfig
    from .releases import OperatorError

    async def execute():
        operator = Operator(OperatorConfig.load(connection_file), state_dir)
        try:
            return await action(operator)
        finally:
            await operator.close()

    try:
        value = asyncio.run(execute())
    except (RpcError, OperatorError) as error:
        raise click.ClickException(str(error)) from None
    except (OSError, ValueError, KeyError, TypeError):
        raise click.ClickException(
            "Invalid operator configuration or private installation state"
        ) from None
    click.echo(json.dumps(value, indent=2))


@supabase.command()
@options
@click.option(
    "--owner-subject",
    type=click.UUID,
    required=True,
    help="Explicit existing Supabase Auth identity to bind as owner.",
)
def install(connection_file, state_dir, owner_subject):
    """Apply migrations, schedule cleanup, bind ownership and export private config.

    Gateway deployment and worker enrollment are separate observable steps.
    """
    run(connection_file, state_dir, lambda operator: operator.install(owner_subject))


@supabase.command()
@options
def upgrade(connection_file, state_dir):
    """Verify drift, apply pending release migrations and preserve identities/keys."""
    from .operator import Installation

    async def action(operator):
        state = Installation.load(state_dir, operator.config)
        return await operator.install(state.owner_subject)

    run(connection_file, state_dir, action)


@supabase.command()
@options
def doctor(connection_file, state_dir):
    """Report schema, workers and gateway independently; never run inference."""
    run(connection_file, state_dir, lambda operator: operator.doctor())


@supabase.command("enroll-worker")
@options
@click.option("--name", required=True)
@click.option("--capacity", type=click.IntRange(1, 64), default=1, show_default=True)
@click.option(
    "--renew",
    is_flag=True,
    help="Explicitly replace an expired/ambiguous enrollment challenge.",
)
def enroll_worker(connection_file, state_dir, name, capacity, renew):
    """Issue a private one-time challenge and worker-only environment file."""
    run(
        connection_file,
        state_dir,
        lambda operator: operator.enroll(name, capacity, renew=renew),
    )


@supabase.command("revoke-worker")
@options
@click.option("--name", required=True)
def revoke_worker(connection_file, state_dir, name):
    """Revoke a journaled installation worker without affecting other clusters."""
    run(connection_file, state_dir, lambda operator: operator.revoke_worker(name))


@supabase.command("rotate-key")
@options
def rotate_key(connection_file, state_dir):
    """Create/verify a new application key, revoke the old key and resume safely."""
    run(connection_file, state_dir, lambda operator: operator.rotate_key())


@supabase.command()
@options
def uninstall(connection_file, state_dir):
    """Disable admission, cancel active work and revoke credentials; preserve data.

    Shared schemas, gateway artifact and cleanup schedules remain installed.
    """
    run(connection_file, state_dir, lambda operator: operator.disable())


@supabase.command("verify-inference")
@options
@click.option(
    "--model", required=True, help="Installed worker model; never downloads a model."
)
def verify_inference(connection_file, state_dir, model):
    """Explicit bounded eight-token inference check through the configured gateway."""
    run(connection_file, state_dir, lambda operator: operator.verify_inference(model))


@supabase.command("export-bundle")
@click.option("--output", type=click.Path(path_type=Path), required=True)
def export_bundle(output):
    """Export verified SQL/Edge artifacts into an empty release directory."""
    from .releases import export_bundle as export, OperatorError

    try:
        manifest = export(output)
    except (OperatorError, OSError) as error:
        raise click.ClickException(
            str(error) if isinstance(error, OperatorError) else "Bundle export failed"
        ) from None
    click.echo(
        json.dumps(
            {
                "release": manifest["release"],
                "directory": str(output.absolute()),
                "contains_credentials": False,
            }
        )
    )


@supabase.command("export-vercel")
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--wheel", type=click.Path(exists=True, path_type=Path), required=True)
def export_vercel(output, wheel):
    """Export a self-contained Vercel candidate with a verified current wheel."""
    from .releases import export_vercel as export, OperatorError

    try:
        result = export(output, wheel)
    except (OperatorError, OSError) as error:
        raise click.ClickException(
            str(error) if isinstance(error, OperatorError) else "Vercel export failed"
        ) from None
    click.echo(json.dumps(result, indent=2))
