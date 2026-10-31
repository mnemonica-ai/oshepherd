"""Guided creation or selection of Supabase projects without external SQL tools."""

from pathlib import Path

import click

from .management import SupabaseManagement
from .operator import OperatorConfig, installation_lock
from .private_files import read_private_json, write_private_json
from .provider_setup import (
    check_new_edge_namespace,
    discover_projects,
    ensure_owner,
    prepare_existing_project,
)
from .provisioning import (
    available_regions,
    creation_record,
    execute_creation,
    organizations,
    organization_is_free,
    plan_creation,
    wait_project,
)
from .releases import OperatorError


def _label(value):
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


async def _free_consent(provider, organization):
    verified = await organization_is_free(provider, organization)
    if verified:
        click.echo(
            "Supabase confirms the selected organization is on Free. No billing upgrades or add-ons will be requested."
        )
        click.confirm(
            "Continue with this verified Free organization", default=False, abort=True
        )
    else:
        click.echo(
            "Supabase did not return its optional billing plan field. Check Free in "
            "your Supabase dashboard before continuing. Oshepherd does not upgrade "
            "billing or purchase add-ons."
        )
        click.confirm(
            "I verified this organization is on Free", default=False, abort=True
        )


def _remember_token(directory, provider):
    write_private_json(
        directory / "supabase-setup-token.json", {"access_token": provider.access_token}
    )


async def _create_project(provider, directory):
    project_plan = creation_record(directory, "project")
    if project_plan is None:
        organization_plan = creation_record(directory, "organization")
        if organization_plan is None:
            choices = await organizations(provider)
            click.echo("0. Create a new organization")
            for index, item in enumerate(choices, 1):
                click.echo(f"{index}. {_label(item['name'])} ({item['slug']})")
            choice = click.prompt(
                "Organization number", type=click.IntRange(0, len(choices))
            )
            if choice:
                organization = choices[choice - 1]
            else:
                name = click.prompt("New organization name", default="oshepherd")
                organization_plan = await plan_creation(
                    provider, directory, "organization", name
                )
        if organization_plan is not None:
            click.echo(
                "Create/resume organization: " + _label(organization_plan["name"])
            )
            click.confirm(
                "Authorize this organization creation (no paid plan or add-ons)",
                default=False,
                abort=True,
            )
            _remember_token(directory, provider)
            organization = await execute_creation(provider, directory, "organization")
        click.echo(
            "Selected organization: "
            + _label(organization["name"])
            + " ("
            + organization["slug"]
            + ")"
        )
        await _free_consent(provider, organization)
        regions = await available_regions(provider, organization)
        for index, region in enumerate(regions, 1):
            click.echo(f"{index}. {_label(region['name'])} ({region['code']})")
        choice = click.prompt("Region number", type=click.IntRange(1, len(regions)))
        name = click.prompt("New project name", default="oshepherd")
        project_plan = await plan_creation(
            provider,
            directory,
            "project",
            name,
            organization=organization,
            region=regions[choice - 1]["code"],
            free_attested=True,
        )
    else:
        organization = project_plan["organization"]
        click.echo(
            "Resume selected organization: "
            + _label(organization["name"])
            + " ("
            + organization["slug"]
            + ")"
        )
        await _free_consent(provider, organization)
        with installation_lock(directory):
            project_plan = creation_record(directory, "project")
            project_plan["free_attested"] = True
            write_private_json(directory / "project-create.json", project_plan)
    click.echo(
        f"Create/resume project {_label(project_plan['name'])}, region {project_plan['region']}. "
        "The unique suffix lets retries find this exact operation; the database password "
        "is generated and saved privately. Provider project limits may prevent creation."
    )
    click.confirm(
        "Authorize this project creation on the verified Free organization",
        default=False,
        abort=True,
    )
    _remember_token(directory, provider)
    project = await execute_creation(provider, directory, "project")
    click.echo(
        "Waiting for the selected Supabase project to become healthy (up to five minutes)."
    )
    return await wait_project(provider, project["id"], organization["id"])


async def guided_existing_setup(directory, *, profile="edge_free", gateway_url=None):
    """Create/select a project and return (config, owner UUID, Supabase PAT path).

    The historical name remains compatible with the original CLI integration.
    SQL bootstrap uses the Management transport; existing private DB URLs remain
    supported on resume but are never requested by this wizard.
    """
    if profile not in {"edge_free", "vercel_hobby"}:
        raise OperatorError("invalid_provider_profile")
    directory = Path(directory).absolute()
    config_file = directory / "connection.json"
    provider_file = directory / "edge-provider.json"
    resuming = (directory / "provider-setup.json").exists()
    saved_token = (
        provider_file
        if provider_file.exists()
        else directory / "supabase-setup-token.json"
    )
    if saved_token.exists():
        token = read_private_json(saved_token)["access_token"]
    else:
        token = click.prompt("Supabase personal access token", hide_input=True).strip()
    provider = SupabaseManagement(_token(token))
    try:
        try:
            await provider.projects()
        except OperatorError as error:
            if str(error) != "provider_access_denied_reauthorize":
                raise
            click.echo(
                "Saved authorization expired or was revoked. Reauthorize your selected Supabase account."
            )
            token = _token(
                click.prompt("Supabase personal access token", hide_input=True).strip()
            )
            await provider.close()
            provider = SupabaseManagement(token)
            await provider.projects()
        database_url = None
        if resuming:
            binding = read_private_json(directory / "provider-setup.json")
            if binding["profile"] != profile:
                raise OperatorError("provider_setup_target_mismatch")
            project = await wait_project(
                provider, binding["project_ref"], binding["organization_id"]
            )
            saved = OperatorConfig.load(config_file) if config_file.exists() else None
            database_url = (
                saved.database_url.get_secret_value()
                if saved and saved.database_url
                else None
            )
            if gateway_url is None and saved:
                gateway_url = saved.gateway_url
        elif creation_record(directory, "project") or creation_record(
            directory, "organization"
        ):
            project = await _create_project(provider, directory)
        else:
            projects = await discover_projects(provider)
            click.echo("0. Create a new Supabase project")
            for index, item in enumerate(projects, 1):
                click.echo(
                    f"{index}. {_label(item['organization_name'])} / {_label(item['name'])} "
                    f"({item['id']}, {_label(item['region'])}, {_label(item['status'])})"
                )
            choice = click.prompt(
                "Project number", type=click.IntRange(0, len(projects))
            )
            if choice:
                selected = projects[choice - 1]
                project = await wait_project(
                    provider, selected["id"], selected["organization_id"]
                )
            else:
                project = await _create_project(provider, directory)
        organization_id = project["organization_id"]
        click.echo(
            f"Target: organization {_label(organization_id)}, project {project['id']}, "
            f"region {_label(project['region'])}; profile {profile}.\n"
            "Setup adds an Oshepherd Auth owner, schemas, private Storage, schedules, "
            "gateway and secrets. It preserves unrelated resources and rejects collisions.\n"
            "Experimental: hosted client disconnect cancellation is not qualified."
        )
        selected_organizations = [
            item
            for item in await organizations(provider)
            if item["id"] == organization_id
        ]
        if len(selected_organizations) != 1:
            raise OperatorError("provider_target_mismatch")
        await _free_consent(provider, selected_organizations[0])
        click.confirm(
            "Authorize setup on this selected project", default=False, abort=True
        )
        config = await prepare_existing_project(
            provider,
            directory,
            project["id"],
            organization_id,
            database_url,
            profile=profile,
            gateway_url=gateway_url,
        )
        if profile == "edge_free":
            await check_new_edge_namespace(provider, config, directory)
        owner = await ensure_owner(config, directory)
        # The bound provider file now contains the credential; remove the temporary
        # recovery copy only after setup finishes successfully.
        temporary = directory / "supabase-setup-token.json"
        if temporary.exists():
            temporary.unlink()
        return config, owner, provider_file
    finally:
        await provider.close()
