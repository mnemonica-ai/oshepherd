"""Lazy CLI entry points for the Supabase operator lifecycle."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
    help="Import an owned 0600 Supabase status JSON file for a loopback stack.",
)
def configure(output, local_from):
    """Create private connection JSON using hidden prompts; refuse overwrites."""
    from .operator import OperatorConfig
    from .private_files import read_private_json, write_private_json
    from .releases import OperatorError

    if output.exists():
        raise click.ClickException(
            "Connection file already exists; edit it privately to change configuration"
        )
    try:
        if local_from:
            local = read_private_json(local_from)
            api = urlsplit(local["API_URL"])
            if "DB_URL" not in local and api.port != 55321:
                raise ValueError("local_database_url_required")
            database = urlsplit(
                local.get(
                    "DB_URL", "postgresql://postgres:postgres@127.0.0.1:55322/postgres"
                )
            )
            if api.hostname != "127.0.0.1" or database.hostname != "127.0.0.1":
                raise ValueError("loopback_required")
            from .releases import database_environment

            database_environment(database.geturl(), "local", local_port=database.port)
            # Local Supabase reserves schema grant ownership for supabase_admin.
            # Use the selected stack's password/port, never another stack's defaults.
            admin_url = urlunsplit(
                database._replace(
                    netloc="supabase_admin:"
                    + database.password
                    + "@127.0.0.1:"
                    + str(database.port)
                )
            )
            values = {
                "project_ref": "local",
                "supabase_url": local["API_URL"],
                "publishable_key": local["PUBLISHABLE_KEY"],
                "secret_key": local["SECRET_KEY"],
                "database_url": admin_url,
                "local_api_port": api.port,
                "local_database_port": database.port,
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
                "profile": "edge_free",
                "exposed_schemas": [
                    name.strip()
                    for name in click.prompt(
                        "Existing exposed schemas from project API settings (comma separated)"
                    ).split(",")
                ],
            }
        config = OperatorConfig.model_validate(values)
        data = config.model_dump(mode="json")
        for name in ("publishable_key", "secret_key", "database_url"):
            if getattr(config, name) is not None:
                data[name] = getattr(config, name).get_secret_value()
        write_private_json(output, data)
    except (OSError, ValueError, KeyError, TypeError, OperatorError):
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
    help="Explicit existing Supabase Auth identity to bind as owner.",
)
@click.option(
    "--create-owner",
    is_flag=True,
    help="Create or resume a private non-login installation owner.",
)
def install(connection_file, state_dir, owner_subject, create_owner):
    """Apply migrations, schedule cleanup, bind ownership and export private config.

    Gateway deployment and worker enrollment are separate observable steps.
    """
    if (owner_subject is None) == (not create_owner):
        raise click.UsageError(
            "Choose exactly one of --owner-subject or --create-owner"
        )

    async def action(operator):
        from .provider_setup import ensure_owner

        subject = owner_subject
        if create_owner:
            subject = await ensure_owner(operator.config, state_dir)
        return await operator.install(subject)

    run(connection_file, state_dir, action)


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


@supabase.command("installer-init")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path(".oshepherd/installer-config.json"),
)
@click.option(
    "--state-dir", type=click.Path(path_type=Path), default=Path(".oshepherd/installer")
)
def installer_init(output, state_dir):
    """Configure the separate installer with hidden secret prompts; no overwrite."""
    from .private_files import write_private_json

    if output.exists():
        raise click.ClickException("Installer configuration already exists")
    try:
        from cryptography.fernet import Fernet
        from .installer import InstallerConfig

        ref = click.prompt("Explicitly authorized Supabase project reference")
        org = click.prompt("Its Supabase organization ID")
        values = {
            "origin": click.prompt(
                "Fixed installer origin", default="http://127.0.0.1:55430"
            ),
            "operator_subject": click.prompt(
                "Installer operator UUID (stable local identity)"
            ),
            "login_secret": click.prompt(
                "Operator access code (at least 32 characters)",
                hide_input=True,
                confirmation_prompt=True,
            ),
            "encryption_key": Fernet.generate_key().decode(),
            "oauth_client_id": click.prompt("Registered Management OAuth app ID"),
            "oauth_client_secret": click.prompt(
                "OAuth app client secret", hide_input=True
            ),
            "allowed_projects": {ref: org},
            "state_dir": str(state_dir.absolute()),
        }
        InstallerConfig.model_validate(values)
        write_private_json(output, values)
    except ImportError:
        raise click.ClickException(
            "Installer needs the supabase and installer extras"
        ) from None
    except (ValueError, OSError):
        raise click.ClickException(
            "Invalid installer configuration; no secrets printed"
        ) from None
    click.echo(
        "Private installer configuration saved; keep it separate from encrypted state backups"
    )


@supabase.command("installer-serve")
@click.option(
    "--config-file", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option("--port", type=click.IntRange(1024, 65535), default=55430)
def installer_serve(config_file, port):
    """Serve the installer on loopback; disable callback/query access logging."""
    try:
        import uvicorn
        from .installer import InstallerConfig, create_installer_app

        config = InstallerConfig.load(config_file)
        uvicorn.run(
            create_installer_app(config),
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
            forwarded_allow_ips="127.0.0.1",
        )
    except ImportError:
        raise click.ClickException(
            "Installer needs the supabase and installer extras"
        ) from None
    except (ValueError, OSError):
        raise click.ClickException(
            "Installer configuration unavailable; no secrets printed"
        ) from None


@supabase.command("installer-work")
@click.option(
    "--config-file", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option(
    "--once", is_flag=True, help="Process at most one queued setup, then exit."
)
def installer_work(config_file, once):
    """Run durable setup independently of browser/gateway invocations."""
    from .releases import OperatorError

    async def work():
        from .installer import Installer, InstallerConfig

        service = Installer(InstallerConfig.load(config_file))
        try:
            while True:
                result = await service.work_once()
                if result:
                    click.echo(json.dumps(result))
                if once:
                    return
                await asyncio.sleep(2)
        finally:
            await service.oauth.close()

    try:
        asyncio.run(work())
    except ImportError:
        raise click.ClickException(
            "Installer needs the supabase and installer extras"
        ) from None
    except OperatorError as error:
        raise click.ClickException(str(error)) from None
    except (ValueError, OSError):
        raise click.ClickException(
            "Installer state unavailable; no secrets printed"
        ) from None


@supabase.command("deploy-edge")
@options
@click.option(
    "--provider-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Private JSON with organization_id and Management access_token.",
)
@click.option(
    "--adopt-existing",
    is_flag=True,
    help="Adopt only after matching the exact artifact, cluster secrets and authenticated health.",
)
def deploy_edge(connection_file, state_dir, provider_file, adopt_existing):
    """Deploy the verified Edge artifact to the explicitly bound owned project."""
    from .releases import OperatorError

    async def deploy():
        from .edge_deployment import EdgeDeployment
        from .management import SupabaseManagement
        from .operator import OperatorConfig
        from .private_files import read_private_json

        values = read_private_json(provider_file)
        if set(values) != {"organization_id", "access_token"} or not all(
            isinstance(value, str) and value for value in values.values()
        ):
            raise OperatorError("invalid_provider_configuration")
        provider = SupabaseManagement(values["access_token"])
        deployment = EdgeDeployment(
            provider,
            OperatorConfig.load(connection_file),
            state_dir,
            values["organization_id"],
        )
        try:
            return await deployment.deploy(adopt_existing=adopt_existing)
        finally:
            await deployment.close()
            await provider.close()

    try:
        click.echo(json.dumps(asyncio.run(deploy())))
    except OperatorError as error:
        raise click.ClickException(str(error)) from None
    except (ValueError, OSError, KeyError):
        raise click.ClickException(
            "Edge deployment state unavailable; no secrets printed"
        ) from None


@supabase.command("plan-edge-removal")
@options
@click.option(
    "--provider-file", type=click.Path(exists=True, path_type=Path), required=True
)
def plan_edge_removal(connection_file, state_dir, provider_file):
    """Print the concrete owned gateway removal plan; do not delete anything."""
    from .releases import OperatorError

    async def plan():
        from .edge_deployment import EdgeDeployment
        from .management import SupabaseManagement
        from .operator import OperatorConfig
        from .private_files import read_private_json

        values = read_private_json(provider_file)
        provider = SupabaseManagement(values["access_token"])
        deployment = EdgeDeployment(
            provider,
            OperatorConfig.load(connection_file),
            state_dir,
            values["organization_id"],
        )
        try:
            return await deployment.removal_plan()
        finally:
            await deployment.close()
            await provider.close()

    try:
        click.echo(json.dumps(asyncio.run(plan()), indent=2))
    except OperatorError as error:
        raise click.ClickException(str(error)) from None
    except (ValueError, OSError, KeyError):
        raise click.ClickException(
            "Removal state unavailable; no secrets printed"
        ) from None
