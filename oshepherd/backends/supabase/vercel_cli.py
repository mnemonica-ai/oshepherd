"""Terminal selection for a free Vercel gateway, without the Vercel CLI."""

from pathlib import Path

import click

from .private_files import read_private_json
from .releases import OperatorError
from .vercel_setup import (
    VercelManagement,
    bind_project,
    create_project,
    creation_intent,
    verified_domains,
)


def _label(value):
    return "".join(char for char in str(value) if char.isprintable())[:160]


async def guided_vercel_setup(directory):
    directory = Path(directory).absolute()
    binding_file = directory / "vercel-setup.json"
    provider_file = directory / "vercel-provider.json"
    saved = read_private_json(binding_file) if binding_file.exists() else None
    token = (
        read_private_json(provider_file)["access_token"]
        if provider_file.exists()
        else click.prompt("Vercel access token", hide_input=True).strip()
    )
    provider = VercelManagement(token)
    try:
        if saved:
            try:
                return await bind_project(
                    provider,
                    directory,
                    saved["team_id"],
                    saved["project_id"],
                    saved["gateway_url"],
                )
            except OperatorError as error:
                if str(error) != "vercel_access_denied":
                    raise
                await provider.close()
                provider = VercelManagement(
                    click.prompt(
                        "Vercel access token expired or denied; new token",
                        hide_input=True,
                    ).strip()
                )
                return await bind_project(
                    provider,
                    directory,
                    saved["team_id"],
                    saved["project_id"],
                    saved["gateway_url"],
                )
        creation_file = directory / "vercel-create.json"
        intent = read_private_json(creation_file) if creation_file.exists() else None
        if intent:
            team_id, mode = intent["team_id"], "create"
            await provider.hobby_team(team_id)
        else:
            teams = await provider.inventory("/v2/teams", "teams")
            teams = [
                team for team in teams if team.get("billing", {}).get("plan") == "hobby"
            ]
            if not teams:
                raise OperatorError("no_vercel_hobby_teams")
            for index, team in enumerate(teams, 1):
                click.echo(
                    f"{index}. {_label(team.get('name') or team.get('slug'))} ({_label(team['id'])}, Hobby)"
                )
            team_id = teams[
                click.prompt("Vercel team number", type=click.IntRange(1, len(teams)))
                - 1
            ]["id"]
            await provider.hobby_team(team_id)
            mode = click.prompt(
                "Vercel project",
                type=click.Choice(["create", "existing"]),
                default="create",
            )
        if mode == "create":
            label = (
                intent["label"]
                if intent
                else click.prompt("New Vercel project name prefix", default="oshepherd")
            )
            intent = creation_intent(directory, team_id, label)
            click.echo(
                f"Create {_label(intent['name'])} in {_label(team_id)} (Hobby), with its free vercel.app domain. No paid upgrade."
            )
            click.confirm(
                "Create or resume this selected project", default=False, abort=True
            )
            project, gateway = await create_project(provider, directory, team_id, label)
        else:
            projects = await provider.inventory(
                "/v9/projects", "projects", team_id=team_id
            )
            if not projects:
                raise OperatorError("no_vercel_projects")
            for index, project in enumerate(projects, 1):
                click.echo(
                    f"{index}. {_label(project['name'])} ({_label(project['id'])})"
                )
            project = projects[
                click.prompt(
                    "Vercel project number", type=click.IntRange(1, len(projects))
                )
                - 1
            ]
            project = await provider.project(team_id, project["id"])
            domains = verified_domains(
                await provider.request(
                    "GET", "/v9/projects/" + project["id"] + "/domains", team_id=team_id
                )
            )
            if not domains:
                raise OperatorError("vercel_verified_production_domain_required")
            gateway = "https://" + (
                domains[0]
                if len(domains) == 1
                else click.prompt(
                    "Verified production domain", type=click.Choice(domains)
                )
            )
        click.echo(
            f"Target: {_label(team_id)} / {_label(project['name'])}; {gateway}. Gateway secrets will be encrypted and Production-only."
        )
        click.confirm(
            "Use this project for the experimental gateway", default=False, abort=True
        )
        return await bind_project(provider, directory, team_id, project["id"], gateway)
    finally:
        await provider.close()
