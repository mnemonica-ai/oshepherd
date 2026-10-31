"""Explicit Hobby project selection and journaled, non-replayed creation."""

import asyncio
import json
from pathlib import Path
import re
from urllib.parse import quote
from uuid import uuid4

import httpx

from .client import bounded_response, RpcError
from .operator import installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError
from .vercel_deployment import VercelTarget


class VercelManagement:
    def __init__(self, token, *, client=None):
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or any(c.isspace() for c in token)
        ):
            raise OperatorError("invalid_vercel_access_token")
        self.token = token
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.owns_client = client is None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def request(self, method, path, *, team_id=None, body=None, **params):
        if method not in ("GET", "POST"):
            raise OperatorError("unsupported_vercel_operation")
        if team_id is not None:
            if not re.fullmatch(r"team_[A-Za-z0-9]+", team_id):
                raise OperatorError("invalid_vercel_team")
            params["teamId"] = team_id
        try:
            async with asyncio.timeout(15):
                async with self.client.stream(
                    method,
                    "https://api.vercel.com" + path,
                    params=params,
                    headers={"Authorization": "Bearer " + self.token},
                    json=body,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (401, 403):
                        raise OperatorError("vercel_access_denied")
                    if response.status_code == 404:
                        raise OperatorError("vercel_resource_missing")
                    if response.status_code not in (200, 201):
                        raise OperatorError("vercel_provider_failed")
                    result = json.loads(
                        await bounded_response(response, 2 * 1024 * 1024)
                    )
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
        except (httpx.HTTPError, TimeoutError):
            raise OperatorError(
                "vercel_provider_unavailable"
                if method == "GET"
                else "vercel_creation_outcome_ambiguous"
            ) from None
        except (ValueError, RpcError):
            raise OperatorError("invalid_vercel_response") from None

    async def inventory(self, path, key, *, team_id=None):
        found, until = [], None
        for _ in range(10):
            page = await self.request(
                "GET",
                path,
                team_id=team_id,
                limit=100,
                **({"until": until} if until else {}),
            )
            items = page.get(key)
            if not isinstance(items, list) or any(
                not isinstance(item, dict) for item in items
            ):
                raise OperatorError("invalid_vercel_response")
            found.extend(items)
            next_page = page.get("pagination", {}).get("next")
            if next_page is None:
                return found
            if next_page == until:
                break
            until = next_page
        raise OperatorError("vercel_inventory_limit")

    async def hobby_team(self, team_id):
        team = await self.request("GET", "/v2/teams/" + team_id, team_id=team_id)
        if (
            team.get("id") != team_id
            or not isinstance(team.get("billing"), dict)
            or team["billing"].get("plan") != "hobby"
        ):
            raise OperatorError("vercel_hobby_team_required")
        return team

    async def project(self, team_id, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", identifier
        ):
            raise OperatorError("invalid_vercel_project")
        project = await self.request(
            "GET", "/v9/projects/" + quote(identifier, safe=""), team_id=team_id
        )
        if project.get("accountId") != team_id or not re.fullmatch(
            r"prj_[A-Za-z0-9]+", str(project.get("id"))
        ):
            raise OperatorError("vercel_project_owner_mismatch")
        if project.get("framework") not in (None, "fastapi") or project.get("link"):
            raise OperatorError("vercel_project_collision")
        return project


def creation_intent(directory, team_id, label):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,29}", label):
        raise OperatorError("invalid_vercel_project_name")
    with installation_lock(directory) as directory:
        path = directory / "vercel-create.json"
        if path.exists():
            intent = read_private_json(path)
            if intent.get("team_id") != team_id or intent.get("label") != label:
                raise OperatorError("vercel_creation_target_mismatch")
            return intent
        operation = uuid4().hex
        intent = {
            "v": 1,
            "team_id": team_id,
            "label": label,
            "operation_id": operation,
            "name": label + "-" + operation,
            "stage": "prepared",
            "project_id": None,
            "domain_creating": False,
        }
        write_private_json(path, intent)
        return intent


async def create_project(provider, directory, team_id, label):
    """The caller confirms the displayed persisted name before this operation."""
    await provider.hobby_team(team_id)
    with installation_lock(directory) as directory:
        path = directory / "vercel-create.json"
        intent = read_private_json(path)
        if (
            intent.get("v") != 1
            or intent.get("team_id") != team_id
            or intent.get("label") != label
            or not re.fullmatch(r"[a-f0-9]{32}", str(intent.get("operation_id")))
            or intent.get("name") != label + "-" + intent["operation_id"]
            or intent.get("stage") not in ("prepared", "creating", "created")
        ):
            raise OperatorError("vercel_creation_target_mismatch")
        try:
            project = await provider.project(
                team_id, intent["project_id"] or intent["name"]
            )
        except OperatorError as error:
            if str(error) != "vercel_resource_missing":
                raise
            project = None
        if project is not None:
            if intent["stage"] == "prepared" or project.get("name") != intent["name"]:
                raise OperatorError("vercel_project_collision")
        elif intent["stage"] != "prepared":
            raise OperatorError("vercel_creation_ambiguous_reconcile_project")
        else:
            intent["stage"] = "creating"
            write_private_json(path, intent)
            acknowledgement = await provider.request(
                "POST",
                "/v11/projects",
                team_id=team_id,
                body={
                    "name": intent["name"],
                    "framework": "fastapi",
                    "previewDeploymentsDisabled": True,
                },
            )
            identifier = acknowledgement.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(
                r"prj_[A-Za-z0-9]+", identifier
            ):
                raise OperatorError("invalid_vercel_creation_acknowledgement")
            project = await provider.project(team_id, identifier)
            if project.get("name") != intent["name"]:
                raise OperatorError("vercel_creation_target_mismatch")
        intent.update(stage="created", project_id=project["id"])
        write_private_json(path, intent)
        domains = await provider.request(
            "GET", "/v9/projects/" + project["id"] + "/domains", team_id=team_id
        )
        expected = intent["name"] + ".vercel.app"
        if expected not in verified_domains(domains):
            if intent["domain_creating"]:
                raise OperatorError("vercel_domain_creation_ambiguous")
            intent["domain_creating"] = True
            write_private_json(path, intent)
            await provider.request(
                "POST",
                "/v10/projects/" + project["id"] + "/domains",
                team_id=team_id,
                body={"name": expected},
            )
            domains = await provider.request(
                "GET", "/v9/projects/" + project["id"] + "/domains", team_id=team_id
            )
        if expected not in verified_domains(domains):
            raise OperatorError("vercel_gateway_domain_not_owned")
        intent["domain_creating"] = False
        write_private_json(path, intent)
        return project, "https://" + expected


def verified_domains(value):
    domains = value.get("domains")
    if not isinstance(domains, list) or any(
        not isinstance(item, dict) for item in domains
    ):
        raise OperatorError("invalid_vercel_response")
    return sorted(
        {
            item["name"]
            for item in domains
            if item.get("verified") is True
            and not item.get("gitBranch")
            and not item.get("redirect")
            and not item.get("customEnvironmentId")
            and isinstance(item.get("name"), str)
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]", item["name"])
        }
    )


async def bind_project(provider, directory, team_id, project_id, gateway_url):
    await provider.hobby_team(team_id)
    project = await provider.project(team_id, project_id)
    if project["id"] != project_id:
        raise OperatorError("vercel_project_owner_mismatch")
    domains = await provider.request(
        "GET", "/v9/projects/" + project_id + "/domains", team_id=team_id
    )
    if gateway_url not in ["https://" + domain for domain in verified_domains(domains)]:
        raise OperatorError("vercel_gateway_domain_not_owned")
    with installation_lock(directory) as directory:
        receipt_file = directory / "vercel-deployment.json"
        if not receipt_file.exists():
            history = await provider.request(
                "GET", "/v6/deployments", team_id=team_id, projectId=project_id, limit=1
            )
            env = await provider.request(
                "GET", "/v9/projects/" + project_id + "/env", team_id=team_id
            )
            environments = env.get("envs")
            if not isinstance(history.get("deployments"), list) or not isinstance(
                environments, list
            ):
                raise OperatorError("invalid_vercel_response")
            if history["deployments"] or any(
                not isinstance(item, dict)
                or item.get("key", "").startswith(("OSHEPHERD_", "SUPABASE_"))
                for item in environments
            ):
                raise OperatorError("vercel_project_collision")
        else:
            receipt = read_private_json(receipt_file)
            if (
                receipt.get("binding", {}).get("team_id") != team_id
                or receipt.get("binding", {}).get("project_id") != project_id
            ):
                raise OperatorError("vercel_receipt_target_mismatch")
        binding = {
            "v": 1,
            "team_id": team_id,
            "project_id": project_id,
            "gateway_url": gateway_url,
        }
        path = directory / "vercel-setup.json"
        if path.exists() and read_private_json(path) != binding:
            raise OperatorError("vercel_setup_target_mismatch")
        write_private_json(path, binding)
        target = VercelTarget(
            team_id=team_id, project_id=project_id, access_token=provider.token
        )
        provider_file = directory / "vercel-provider.json"
        write_private_json(
            provider_file,
            {
                "team_id": target.team_id,
                "project_id": target.project_id,
                "access_token": provider.token,
            },
        )
        return gateway_url, provider_file
