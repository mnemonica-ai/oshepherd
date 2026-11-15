"""User-facing sharing and private invitation commands; imports stay lazy."""

import asyncio
from pathlib import Path

import click


async def deploy_selected(operator, provider_file):
    """Use the existing ownership adapters without implicit adoption."""
    from .private_files import read_private_json
    from .releases import OperatorError

    from .edge_deployment import EdgeDeployment
    from .management import SupabaseManagement

    values = read_private_json(provider_file)
    if (
        not isinstance(values, dict)
        or set(values) != {"organization_id", "access_token"}
        or not all(isinstance(value, str) and value for value in values.values())
    ):
        raise OperatorError("invalid_provider_configuration")
    provider = SupabaseManagement(values["access_token"])
    deployment = None
    try:
        deployment = EdgeDeployment(
            provider, operator.config, operator.directory, values["organization_id"]
        )
        return await deployment.deploy()
    finally:
        if deployment is not None:
            await deployment.close()
        await provider.close()


def report_error(error):
    from .client import RpcError
    from .onboarding import OnboardingError
    from .releases import OperatorError

    if isinstance(error, RpcError):
        message = error.code
    elif isinstance(error, (OperatorError, OnboardingError)):
        message = str(error)
    else:
        message = "Sharing setup failed; check the selected target and private state"
    return click.ClickException(message)


def configured_selection(directory):
    """Resume a completed wizard without depending on its Management token."""
    from .operator import Installation, OperatorConfig
    from .private_files import read_private_json
    from .releases import OperatorError

    names = (
        "provider-setup.json",
        "provider-owner.json",
        "connection.json",
        "installation.json",
    )
    if not all((directory / name).exists() for name in names):
        return None
    config = OperatorConfig.load(directory / "connection.json")
    state = Installation.load(directory, config)
    binding = read_private_json(directory / "provider-setup.json")
    owner = read_private_json(directory / "provider-owner.json")
    if (
        not isinstance(binding, dict)
        or binding.get("v") != 1
        or binding.get("project_ref") != config.project_ref
        or binding.get("profile") != config.profile
        or not isinstance(binding.get("organization_id"), str)
        or not binding["organization_id"]
        or not isinstance(owner, dict)
        or owner.get("v") != 1
        or owner.get("project_ref") != config.project_ref
        or owner.get("supabase_url") != config.supabase_url
        or owner.get("stage") != "ready"
        or owner.get("owner_subject") != str(state.owner_subject)
    ):
        raise OperatorError("provider_setup_target_mismatch")
    if state.stage == "disabled":
        raise OperatorError("installation_disabled")
    if state.stage != "configured":
        return None
    # Configured SQL precedes deployment. Resume interrupted deployment through
    # the selected provider; a completed receipt needs no Management access.
    receipt_file = directory / "edge-deployment.json"
    if not receipt_file.exists():
        return None
    receipt = read_private_json(receipt_file)
    if (
        not isinstance(receipt, dict)
        or receipt.get("project_ref") != config.project_ref
        or receipt.get("cluster_id") != str(state.cluster_id)
        or receipt.get("organization_id") != binding["organization_id"]
    ):
        raise OperatorError("edge_receipt_target_mismatch")
    return config if receipt.get("stage") == "deployed" else None


@click.command()
@click.option(
    "--connection-file",
    type=click.Path(exists=True, path_type=Path),
    help="Existing owned 0600 operator connection; omit for guided existing-project setup.",
)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path),
    help="Private resumable operator state (default: user state directory).",
)
@click.option(
    "--owner-subject",
    type=click.UUID,
    help="Existing Auth owner for an initial prepared connection; inferred on resume.",
)
@click.option(
    "--provider-file",
    type=click.Path(exists=True, path_type=Path),
    help="Private selected provider configuration for gateway deployment.",
)
@click.option(
    "--gateway",
    type=click.Choice(["edge"]),
    help="Gateway for guided setup; defaults to Edge and remembers the selected profile.",
)
@click.option(
    "--database-provider-file",
    type=click.Path(exists=True, path_type=Path),
    help="Private Supabase provider file for Management SQL when using a prepared connection without a database URL.",
)
@click.option("--ollama-url", default="http://127.0.0.1:11434", show_default=True)
@click.option(
    "--model", help="Installed model used for the first inference check; no downloads."
)
@click.option("--worker-name", default="local", show_default=True)
@click.option("--capacity", type=click.IntRange(1, 64), default=1, show_default=True)
@click.option(
    "--renew-enrollment",
    is_flag=True,
    help="Explicitly replace an expired/ambiguous challenge when no worker session exists.",
)
@click.option(
    "--check",
    is_flag=True,
    help="Verify real inference and stop the worker/cleanup instead of staying in the foreground.",
)
def share(
    connection_file,
    state_dir,
    owner_subject,
    provider_file,
    gateway,
    database_provider_file,
    ollama_url,
    model,
    worker_name,
    capacity,
    renew_enrollment,
    check,
):
    """Share local Ollama through an experimental Supabase installation.

    Keep this command running to serve requests and clean up retained Storage.
    Create or select a Free Supabase project. Credentials
    are requested privately; setup requires no database password or provider CLI.
    """
    from .client import RpcError
    from .onboarding import default_state_dir, discover_ollama
    from .operator import Installation, Operator, OperatorConfig
    from .private_files import read_private_json
    from .releases import OperatorError
    from .sharing import gateway_url, run_share

    async def execute():
        discovery = await discover_ollama(ollama_url, model=model)
        selected_model = discovery.model
        if selected_model is None:
            selected_model = click.prompt(
                "Installed model for the inference check",
                type=click.Choice(list(discovery.models)),
            )
        selected_owner, selected_provider = owner_subject, provider_file
        sql_provider = database_provider_file
        if connection_file is None:
            config = configured_selection(state_dir)
            if config is None:
                from .provider_cli import guided_existing_setup

                binding_path = state_dir / "provider-setup.json"
                saved_profile = (
                    read_private_json(binding_path)["profile"]
                    if binding_path.exists()
                    else None
                )
                profile = {"edge": "edge_free"}.get(gateway)
                if saved_profile and profile and profile != saved_profile:
                    raise OperatorError("provider_setup_target_mismatch")
                profile = saved_profile or profile or "edge_free"
                config, generated_owner, generated_provider = (
                    await guided_existing_setup(state_dir, profile=profile)
                )
                if selected_owner is not None and selected_owner != generated_owner:
                    raise OperatorError("installation_owner_mismatch")
                selected_owner = generated_owner
                sql_provider = sql_provider or generated_provider
                selected_provider = selected_provider or generated_provider
        else:
            config = OperatorConfig.load(connection_file)
        if gateway and config.profile != {"edge": "edge_free"}[gateway]:
            raise OperatorError("provider_setup_target_mismatch")
        operator = Operator(config, state_dir)
        database = None

        def progress(event):
            messages = {
                "cloud_ready": "Cloud ready: schema and authenticated gateway verified.",
                "worker_connected": "Worker connected; local models registered.",
                "maintenance_ready": "Storage cleanup active; first cycle completed.",
                "inference_verified": (
                    "Inference verified. Endpoint: "
                    + gateway_url(config)
                    + "\nPrivate client configuration: "
                    + str(state_dir / "client.json")
                ),
                "sharing_foreground": "Sharing in the foreground. Keep this terminal open; Ctrl+C stops the worker and cleanup.",
            }
            click.echo(messages[event])

        try:
            state = (
                Installation.load(state_dir, config)
                if (state_dir / "installation.json").exists()
                else None
            )
            if config.database_url is None and (
                state is None or state.stage != "configured"
            ):
                from .management_database import ManagementDatabase

                path = sql_provider or state_dir / "edge-provider.json"
                if not path.exists():
                    raise OperatorError(
                        "supabase_management_authorization_required_for_setup"
                    )
                values = read_private_json(path)
                database = ManagementDatabase(
                    config, values["access_token"], values["organization_id"]
                )
            click.echo(
                "Experimental hosting: disconnect cancellation remains unqualified."
            )
            result = await run_share(
                operator,
                model=selected_model,
                ollama_url=discovery.url,
                owner_subject=selected_owner,
                worker_name=worker_name,
                capacity=capacity,
                renew_enrollment=renew_enrollment,
                database=database,
                deploy=(
                    (lambda: deploy_selected(operator, selected_provider))
                    if selected_provider
                    else None
                ),
                check=check,
                on_event=progress,
            )
            click.echo(
                "Sharing check complete; worker and cleanup stopped."
                if check and result.get("inference_verified")
                else "Worker and cleanup stopped."
            )
        finally:
            if database is not None:
                database.close()
            await operator.close()

    try:
        state_dir = (state_dir or default_state_dir("share")).absolute()
        asyncio.run(execute())
    except (
        RpcError,
        OperatorError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        TimeoutError,
    ) as error:
        raise report_error(error) from None


@click.command()
@click.option(
    "--connection-file",
    type=click.Path(exists=True, path_type=Path),
    help="Private operator connection; defaults to connection.json in --state-dir.",
)
@click.option("--state-dir", type=click.Path(path_type=Path))
@click.option("--name", required=True, help="Stable name for the additional worker.")
@click.option("--capacity", type=click.IntRange(1, 64), default=1, show_default=True)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    required=True,
    help="Private invitation destination; refuses existing files.",
)
@click.option(
    "--renew",
    is_flag=True,
    help="Explicitly replace an expired or previously consumed challenge.",
)
def invite(connection_file, state_dir, name, capacity, output, renew):
    """Create a private one-time invitation for another machine to join."""
    from .client import RpcError
    from .onboarding import default_state_dir, export_invitation
    from .operator import Operator, OperatorConfig
    from .releases import OperatorError

    async def execute():
        if output.exists() or output.is_symlink():
            raise OperatorError("invitation_destination_exists")
        config = OperatorConfig.load(connection_file or state_dir / "connection.json")
        operator = Operator(config, state_dir)
        try:
            result = await operator.enroll(name, capacity, renew=renew)
            export_invitation(config, result["enrollment_file"], capacity, output)
        finally:
            await operator.close()

    try:
        state_dir = (state_dir or default_state_dir("share")).absolute()
        asyncio.run(execute())
    except (
        RpcError,
        OperatorError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        TimeoutError,
    ) as error:
        raise report_error(error) from None
    click.echo("Private invitation saved: " + str(output.absolute()))
    click.echo(
        "Transfer it privately, then run oshepherd join --invitation-file on the other machine. No administrator credentials are included."
    )
