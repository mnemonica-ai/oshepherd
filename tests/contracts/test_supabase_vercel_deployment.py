"""Provider-scoped CLI configuration and lost deployment acknowledgement tests."""

import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from oshepherd.backends.supabase.operator import OperatorConfig, Installation
from oshepherd.backends.supabase.private_files import read_private_json
from oshepherd.backends.supabase.releases import OperatorError
from oshepherd.backends.supabase.vercel_deployment import VercelDeployment, VercelTarget

TEAM, PROJECT = "team_Isolated", "prj_Isolated"
URL = "https://owned.vercel.app"


class Cloud:
    def __init__(self):
        self.plan = "hobby"
        self.account = TEAM
        self.domain = "owned.vercel.app"
        self.envs = [
            {
                "id": "unrelated",
                "key": "UNRELATED",
                "type": "plain",
                "target": ["preview"],
            }
        ]
        self.deployment = None
        self.calls = []
        self.cli_calls = []
        self.deploys = 0
        self.fail_environment = False
        self.lose_ack = False
        self.failed_ack = False
        self.disabled = False
        self.lose_remove_ack = False

    def http(self, request):
        self.calls.append(request)
        if request.url.host == "owned.vercel.app":
            assert "provider-token" not in request.headers.get("Authorization", "")
            return httpx.Response(
                200 if request.headers.get("Authorization") else 401,
                json={"status": 200},
            )
        assert request.url.host == "api.vercel.com"
        assert request.url.params["teamId"] == TEAM
        assert request.headers["Authorization"] == "Bearer provider-token"
        path = request.url.path
        if request.method == "DELETE":
            assert self.disabled and path == "/v13/deployments/dpl_Isolated01"
            self.deployment = None
            if self.lose_remove_ack:
                self.lose_remove_ack = False
                raise httpx.ReadTimeout(
                    "private deletion acknowledgement lost", request=request
                )
            return httpx.Response(200, json={})
        if path.startswith("/v2/teams/"):
            return httpx.Response(
                200,
                json={
                    "id": TEAM,
                    "slug": "isolated-team",
                    "billing": {"plan": self.plan},
                },
            )
        if path.endswith("/domains"):
            return httpx.Response(
                200, json={"domains": [{"name": self.domain, "verified": True}]}
            )
        if path.endswith("/env"):
            return httpx.Response(200, json={"envs": self.envs})
        if path.startswith("/v9/projects/"):
            return httpx.Response(
                200,
                json={
                    "id": PROJECT,
                    "accountId": self.account,
                    "name": "owned",
                    "framework": "fastapi",
                },
            )
        if path == "/v6/deployments":
            items = (
                [dict(self.deployment, uid=self.deployment["id"])]
                if self.deployment
                else []
            )
            return httpx.Response(
                200, json={"deployments": items, "pagination": {"next": None}}
            )
        if path.startswith("/v13/deployments/"):
            if self.deployment is None:
                return httpx.Response(404, json={"error": "not_found"})
            return httpx.Response(200, json=self.deployment)
        raise AssertionError("Unexpected provider route")

    def cli(self, command, **kwargs):
        self.cli_calls.append(command)
        auth_folder = Path(command[command.index("--global-config") + 1])
        assert read_private_json(auth_folder / "auth.json") == {
            "token": "provider-token"
        }
        assert "provider-token" not in " ".join(command)
        assert kwargs["env"]["VERCEL_ORG_ID"] == TEAM
        assert kwargs["env"]["VERCEL_PROJECT_ID"] == PROJECT
        assert "VERCEL_TOKEN" not in kwargs["env"]
        bundle = Path(command[command.index("--cwd") + 1])
        assert auth_folder not in bundle.parents and bundle not in auth_folder.parents
        assert not list(bundle.rglob("*.env"))
        assert (
            read_private_json(bundle / ".vercel/project.json")["projectId"] == PROJECT
        )
        if command[1:3] == ["env", "add"]:
            key = command[3]
            assert command[4] == "production" and "--force" in command
            assert isinstance(kwargs["input"], str)
            if key == "SUPABASE_SECRET_KEY":
                assert kwargs["input"] not in command
            self.envs = [item for item in self.envs if item["key"] != key]
            self.envs.append(
                {
                    "id": "env-" + key,
                    "key": key,
                    "type": "encrypted",
                    "target": ["production"],
                }
            )
            if self.fail_environment:
                self.fail_environment = False
                return SimpleNamespace(
                    returncode=1, stdout="private output", stderr="private value"
                )
            return SimpleNamespace(returncode=0, stdout="added", stderr="")
        assert command[1] == "deploy" and "--prod" in command
        self.deploys += 1
        meta = dict(
            command[index + 1].split("=", 1)
            for index, value in enumerate(command)
            if value == "--meta"
        )
        self.deployment = {
            "id": "dpl_Isolated01",
            "projectId": PROJECT,
            "meta": meta,
            "target": "production",
            "readyState": "READY",
        }
        if self.lose_ack:
            self.lose_ack = False
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if self.failed_ack:
            self.failed_ack = False
            return SimpleNamespace(
                returncode=1, stdout="private provider failure", stderr="private value"
            )
        return SimpleNamespace(
            returncode=0, stdout="https://owned-operation.vercel.app", stderr=""
        )


def fixture(tmp_path, monkeypatch):
    def export(bundle, wheel):
        bundle.mkdir(parents=True)
        (bundle / "app.py").write_text("verified fixture export")

    monkeypatch.setattr(
        "oshepherd.backends.supabase.vercel_deployment.export_vercel", export
    )
    target = VercelTarget(
        team_id=TEAM, project_id=PROJECT, access_token="provider-token"
    )
    config = OperatorConfig(
        project_ref="a" * 20,
        supabase_url="https://" + "a" * 20 + ".supabase.co",
        publishable_key="sb_publishable_" + "p" * 32,
        secret_key="sb_secret_" + "s" * 32,
        profile="vercel_hobby",
        gateway_url=URL,
    )
    directory = tmp_path / "operator"
    state = Installation(
        project_ref=config.project_ref,
        supabase_url=config.supabase_url,
        profile="vercel_hobby",
        owner_subject=uuid4(),
        stage="configured",
    )
    state.save(directory)
    wheel = tmp_path / "verified.whl"
    wheel.write_bytes(b"verified-wheel-fixture")
    cloud = Cloud()
    client = httpx.AsyncClient(transport=httpx.MockTransport(cloud.http))
    deployment = VercelDeployment(
        target, config, directory, client=client, runner=cloud.cli
    )
    return cloud, client, deployment, wheel, state


@pytest.mark.parametrize("fault", ["lose_ack", "failed_ack", "fail_environment"])
def test_cli_failure_reconciles_scoped_encrypted_configuration_and_one_deployment(
    tmp_path, monkeypatch, fault
):
    async def run():
        cloud, client, adapter, wheel, state = fixture(tmp_path, monkeypatch)
        setattr(cloud, fault, True)
        async with client:
            with pytest.raises(OperatorError, match="vercel_cli_") as failure:
                await adapter.deploy(wheel)
            assert "private" not in str(failure.value)
            result = await adapter.deploy(wheel)
            assert result["gateway_verified"] and not result["hosted_qualified"]
            assert cloud.deploys == 1
            assert (await adapter.deploy(wheel)) == result
            assert cloud.deploys == 1
            assert {item["key"] for item in cloud.envs} == {
                "UNRELATED",
                *adapter.values(state),
            }
            assert cloud.envs[0]["key"] == "UNRELATED" and cloud.envs[0]["target"] == [
                "preview"
            ]
            assert not list((adapter.directory / "provider-auth").glob("*/auth.json"))
            assert "provider-token" not in adapter.receipt_file.read_text()
            assert (
                adapter.config.secret_key.get_secret_value()
                not in adapter.receipt_file.read_text()
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "change, error",
    [
        ("plan", "vercel_hobby_team_required"),
        ("account", "vercel_project_owner_mismatch"),
        ("domain", "vercel_gateway_domain_not_owned"),
    ],
)
def test_plan_project_and_domain_are_verified_before_any_secret_write(
    tmp_path, monkeypatch, change, error
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        setattr(cloud, change, "foreign")
        async with client:
            with pytest.raises(OperatorError, match=error):
                await adapter.deploy(wheel)
            assert not cloud.cli_calls
            assert not adapter.receipt_file.exists()

    asyncio.run(run())


def test_preview_credentials_or_unowned_project_cannot_be_adopted(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        cloud.envs.append(
            {
                "id": "shared",
                "key": "SUPABASE_SECRET_KEY",
                "type": "encrypted",
                "target": ["production", "preview"],
            }
        )
        async with client:
            with pytest.raises(OperatorError, match="vercel_environment_collision"):
                await adapter.deploy(wheel, adopt_existing=True)
            cloud.envs[-1]["target"] = ["production"]
            with pytest.raises(OperatorError, match="vercel_project_collision"):
                await adapter.deploy(wheel)
            assert not cloud.cli_calls

    asyncio.run(run())


def test_unknown_creation_outcome_never_creates_another_deployment(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        cloud.lose_ack = True
        async with client:
            with pytest.raises(OperatorError):
                await adapter.deploy(wheel)
            cloud.deployment = None  # Provider reconciliation cannot find it.
            with pytest.raises(OperatorError, match="vercel_deployment_ambiguous"):
                await adapter.deploy(wheel)
            assert cloud.deploys == 1

    asyncio.run(run())


def test_removal_checks_exact_receipt_disables_first_and_reconciles_lost_ack(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, state = fixture(tmp_path, monkeypatch)

        async def disable():
            cloud.disabled = True
            state.stage = "disabled"
            state.save(adapter.directory)

        operator = SimpleNamespace(
            directory=adapter.directory.absolute(),
            config=adapter.config,
            disable=disable,
        )
        async with client:
            result = await adapter.deploy(wheel)
            identifier = result["deployment_id"]
            with pytest.raises(OperatorError, match="vercel_removal_target_mismatch"):
                await adapter.remove(expected_deployment_id="wrong", operator=operator)
            assert not cloud.disabled
            before = list(cloud.envs)
            cloud.lose_remove_ack = True
            with pytest.raises(OperatorError, match="vercel_removal_outcome_ambiguous"):
                await adapter.remove(
                    expected_deployment_id=identifier, operator=operator
                )
            assert cloud.disabled and cloud.deployment is None
            assert (
                await adapter.remove(
                    expected_deployment_id=identifier, operator=operator
                )
            )["owned_deployment_removed"]
            assert cloud.envs == before
            assert (
                await adapter.remove(
                    expected_deployment_id=identifier, operator=operator
                )
            )["owned_deployment_removed"]

    asyncio.run(run())
