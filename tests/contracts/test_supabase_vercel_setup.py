"""Fresh/existing Hobby selection and ambiguous create outcomes are bounded."""

import asyncio
import json

import httpx
import pytest

from oshepherd.backends.supabase.private_files import read_private_json
from oshepherd.backends.supabase.releases import OperatorError
from oshepherd.backends.supabase.vercel_setup import (
    VercelManagement,
    bind_project,
    create_project,
    creation_intent,
    verified_domains,
)

TEAM = "team_Selected"


class Cloud:
    def __init__(self):
        self.projects = {}
        self.domains = {}
        self.requests = []
        self.plan = "hobby"
        self.lose_ack = False
        self.history = []
        self.environments = []

    def request(self, request):
        self.requests.append(request)
        assert request.url.host == "api.vercel.com"
        assert request.headers["Authorization"] == "Bearer private-provider-token"
        assert request.url.params["teamId"] == TEAM
        path = request.url.path
        if path.startswith("/v2/teams/"):
            return httpx.Response(
                200, json={"id": TEAM, "billing": {"plan": self.plan}}
            )
        if path == "/v11/projects":
            assert request.method == "POST"
            body = json.loads(request.content)
            assert (
                body["framework"] == "fastapi"
                and body["previewDeploymentsDisabled"] is True
            )
            project = {
                "id": "prj_Selected",
                "accountId": TEAM,
                "name": body["name"],
                "framework": "fastapi",
            }
            self.projects[project["id"]] = project
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadTimeout("private-provider-token", request=request)
            return httpx.Response(201, json=project)
        if path.endswith("/domains"):
            identifier = path.split("/")[-2]
            if request.method == "POST":
                body = json.loads(request.content)
                self.domains[identifier] = [{"name": body["name"], "verified": True}]
                return httpx.Response(200, json=self.domains[identifier][0])
            return httpx.Response(
                200, json={"domains": self.domains.get(identifier, [])}
            )
        if path == "/v6/deployments":
            return httpx.Response(200, json={"deployments": self.history})
        if path.endswith("/env"):
            return httpx.Response(200, json={"envs": self.environments})
        if path.startswith("/v9/projects/"):
            identifier = path.split("/")[-1]
            candidates = [
                p for p in self.projects.values() if identifier in (p["id"], p["name"])
            ]
            return (
                httpx.Response(200, json=candidates[0])
                if candidates
                else httpx.Response(404, json={})
            )
        raise AssertionError(path)


def test_lost_project_create_ack_reconciles_exact_unique_name_once(tmp_path):
    async def run():
        cloud = Cloud()
        intent = creation_intent(tmp_path, TEAM, "oshepherd")
        assert creation_intent(tmp_path, TEAM, "oshepherd") == intent
        cloud.lose_ack = True
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(cloud.request)
        ) as client:
            provider = VercelManagement("private-provider-token", client=client)
            with pytest.raises(
                OperatorError, match="vercel_creation_outcome_ambiguous"
            ) as error:
                await create_project(provider, tmp_path, TEAM, "oshepherd")
            assert "private-provider-token" not in str(error.value)
            project, gateway = await create_project(
                provider, tmp_path, TEAM, "oshepherd"
            )
            assert gateway == "https://" + intent["name"] + ".vercel.app"
            assert (await create_project(provider, tmp_path, TEAM, "oshepherd"))[
                0
            ] == project
            _, provider_file = await bind_project(
                provider, tmp_path, TEAM, project["id"], gateway
            )
            assert read_private_json(provider_file)["project_id"] == project["id"]
            assert (
                sum(
                    r.method == "POST" and r.url.path == "/v11/projects"
                    for r in cloud.requests
                )
                == 1
            )
            assert (
                sum(
                    r.method == "POST" and r.url.path.endswith("/domains")
                    for r in cloud.requests
                )
                == 1
            )
            assert (
                "private-provider-token"
                not in (tmp_path / "vercel-create.json").read_text()
            )

    asyncio.run(run())


def test_unknown_create_outcome_never_posts_another_project(tmp_path):
    async def run():
        cloud = Cloud()
        cloud.lose_ack = True
        creation_intent(tmp_path, TEAM, "oshepherd")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(cloud.request)
        ) as client:
            provider = VercelManagement("private-provider-token", client=client)
            with pytest.raises(OperatorError):
                await create_project(provider, tmp_path, TEAM, "oshepherd")
            cloud.projects.clear()
            with pytest.raises(
                OperatorError, match="vercel_creation_ambiguous_reconcile_project"
            ):
                await create_project(provider, tmp_path, TEAM, "oshepherd")
            assert sum(r.method == "POST" for r in cloud.requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "problem", ["paid", "owner", "domain", "history", "environment"]
)
def test_existing_target_checks_reject_before_saving_credentials(tmp_path, problem):
    async def run():
        cloud = Cloud()
        cloud.projects["prj_Selected"] = {
            "id": "prj_Selected",
            "name": "selected",
            "accountId": TEAM,
            "framework": "fastapi",
        }
        cloud.domains["prj_Selected"] = [
            {"name": "selected.vercel.app", "verified": True}
        ]
        if problem == "paid":
            cloud.plan = "pro"
        if problem == "owner":
            cloud.projects["prj_Selected"]["accountId"] = "team_Foreign"
        if problem == "domain":
            cloud.domains["prj_Selected"][0]["verified"] = False
        if problem == "history":
            cloud.history = [{"uid": "dpl_Unrelated"}]
        if problem == "environment":
            cloud.environments = [
                {"key": "SUPABASE_SECRET_KEY", "target": ["production"]}
            ]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(cloud.request)
        ) as client:
            provider = VercelManagement("private-provider-token", client=client)
            with pytest.raises(OperatorError):
                await bind_project(
                    provider,
                    tmp_path,
                    TEAM,
                    "prj_Selected",
                    "https://selected.vercel.app",
                )
            assert not (tmp_path / "vercel-provider.json").exists()
            assert not any(r.method == "POST" for r in cloud.requests)

    asyncio.run(run())


def test_project_name_fits_single_dns_label(tmp_path):
    assert len(creation_intent(tmp_path, TEAM, "a" * 30)["name"]) == 63
    with pytest.raises(OperatorError, match="invalid_vercel_project_name"):
        creation_intent(tmp_path / "bad", TEAM, "a" * 31)


def test_preexisting_project_at_prepared_random_name_is_not_adopted(tmp_path):
    async def run():
        cloud = Cloud()
        intent = creation_intent(tmp_path, TEAM, "oshepherd")
        cloud.projects["prj_Selected"] = {
            "id": "prj_Selected",
            "name": intent["name"],
            "accountId": TEAM,
            "framework": "fastapi",
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(cloud.request)
        ) as client:
            provider = VercelManagement("private-provider-token", client=client)
            with pytest.raises(OperatorError, match="vercel_project_collision"):
                await create_project(provider, tmp_path, TEAM, "oshepherd")
            assert not any(r.method == "POST" for r in cloud.requests)

    asyncio.run(run())


def test_redirect_preview_unverified_domains_are_not_gateway_choices():
    assert verified_domains(
        {
            "domains": [
                {"name": "production.vercel.app", "verified": True},
                {"name": "branch.vercel.app", "verified": True, "gitBranch": "main"},
                {
                    "name": "redirect.vercel.app",
                    "verified": True,
                    "redirect": "evil.example",
                },
                {
                    "name": "custom.vercel.app",
                    "verified": True,
                    "customEnvironmentId": "env_123",
                },
                {"name": "unverified.vercel.app", "verified": False},
            ]
        }
    ) == ["production.vercel.app"]
