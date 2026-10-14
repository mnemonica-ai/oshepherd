"""Terminal prompts for the existing Supabase project onboarding slice."""

from pathlib import Path
import shutil

import click

from .management import SupabaseManagement
from .operator import OperatorConfig
from .private_files import read_private_json
from .provider_setup import (
    check_new_edge_namespace,
    discover_projects,
    ensure_owner,
    prepare_existing_project,
)
from .releases import OperatorError


def _label(value):
    # Provider names are untrusted terminal text, never terminal instructions.
    return "".join(char for char in str(value) if char.isprintable())[:160]


def _token(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(char.isspace() for char in value)
    ):
        raise OperatorError("invalid_provider_access_token")
    return value


async def guided_existing_setup(directory, *, profile="edge_free"):
    """Return (config, owner UUID, provider path) after explicit target consent."""
    if profile != "edge_free":
        raise OperatorError("guided_vercel_setup_not_available")
    if shutil.which("psql") is None:
        raise OperatorError("psql_required_for_project_setup")
    directory = Path(directory).absolute()
    config_file = directory / "connection.json"
    provider_file = directory / "edge-provider.json"
    resuming = (directory / "provider-setup.json").exists()
    if resuming and provider_file.exists():
        provider_values = read_private_json(provider_file)
        token = provider_values["access_token"]
    else:
        click.echo(
            "Connect an existing Supabase project. New project creation is not available yet."
        )
        token = click.prompt("Supabase personal access token", hide_input=True).strip()
    provider = SupabaseManagement(_token(token))
    try:
        if resuming:
            binding = read_private_json(directory / "provider-setup.json")
            try:
                project = await provider.project(
                    binding["project_ref"], binding["organization_id"]
                )
            except OperatorError as error:
                if str(error) != "provider_access_denied_reauthorize":
                    raise
                click.echo(
                    "Saved provider authorization expired or was revoked. Reauthorize the same selected project."
                )
                token = _token(
                    click.prompt(
                        "Supabase personal access token", hide_input=True
                    ).strip()
                )
                await provider.close()
                provider = SupabaseManagement(token)
                project = await provider.project(
                    binding["project_ref"], binding["organization_id"]
                )
            organization_id = binding["organization_id"]
            saved = OperatorConfig.load(config_file) if config_file.exists() else None
            database_url = (
                saved.database_url.get_secret_value()
                if saved and saved.database_url
                else click.prompt(
                    "Postgres connection URL (including password)", hide_input=True
                )
            )
        else:
            projects = await discover_projects(provider)
            if not projects:
                raise OperatorError("no_accessible_supabase_projects")
            for index, item in enumerate(projects, 1):
                click.echo(
                    f"{index}. {_label(item['organization_name'])} / {_label(item['name'])} "
                    f"({item['id']}, {_label(item['region'])}, {_label(item['status'])})"
                )
            selected = click.prompt(
                "Project number", type=click.IntRange(1, len(projects))
            )
            project = projects[selected - 1]
            organization_id = project["organization_id"]
            await provider.project(project["id"], organization_id)
            database_url = click.prompt(
                "Postgres connection URL (including password)", hide_input=True
            )
        click.echo(
            f"Target: organization {_label(organization_id)}, project {project['id']}, "
            f"region {_label(project['region'])}; Supabase Edge gateway.\n"
            "Setup adds an Oshepherd Auth owner, schemas, private Storage, schedules, "
            "gateway and secrets. It preserves unrelated resources and rejects collisions.\n"
            "Experimental: hosted client disconnect cancellation is not qualified.\n"
            "The API used here does not verify billing. Confirm this project's organization "
            "is on Free in your Supabase dashboard. Setup makes no billing changes."
        )
        click.confirm(
            "I verified the Free plan and authorize setup on this selected project",
            default=False,
            abort=True,
        )
        config = await prepare_existing_project(
            provider, directory, project["id"], organization_id, database_url
        )
        await check_new_edge_namespace(provider, config, directory)
        owner = await ensure_owner(config, directory)
        return config, owner, provider_file
    finally:
        await provider.close()
