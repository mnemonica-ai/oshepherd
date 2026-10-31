"""Provider-scoped REST configuration and lost deployment acknowledgement tests."""

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
        self.malformed_ack = False
        self.corrupt_environment = False

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
        if request.method == "POST":
            body = json.loads(request.content)
            if path == "/v10/projects/" + PROJECT + "/env":
                assert request.url.params["upsert"] == "true"
                for item in body:
                    assert item["type"] == "encrypted" and item["target"] == [
                        "production"
                    ]
                    self.envs = [old for old in self.envs if old["key"] != item["key"]]
                    self.envs.append(
                        {key: value for key, value in item.items() if key != "value"}
                    )
                if self.fail_environment:
                    self.fail_environment = False
                    return httpx.Response(500, json={"private": "secret"})
                if self.corrupt_environment:
                    self.envs[-1]["target"] = ["preview"]
                return httpx.Response(200, json={"created": []})
            assert path == "/v13/deployments"
            assert body["project"] == PROJECT and body["target"] == "production"
            assert set(item["file"] for item in body["files"]) == {
                "app.py",
                "vercel.json",
                "runtime-requirements.txt",
                ".python-version",
                ".vercelignore",
                "pyproject.toml",
                "requirements.txt",
                "uv.lock",
                "oshepherd-manifest.json",
                "vendor/verified.whl",
            }
            assert all(item["encoding"] == "base64" for item in body["files"])
            assert "provider-token" not in request.content.decode()
            self.deploys += 1
            self.deployment = {
                "id": "dpl_Isolated01",
                "projectId": PROJECT,
                "meta": body["meta"],
                "target": "production",
                "readyState": "READY",
            }
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadTimeout("private deployment ack", request=request)
            if self.failed_ack:
                self.failed_ack = False
                return httpx.Response(500, json={"private": "secret"})
            if self.malformed_ack:
                self.malformed_ack = False
                return httpx.Response(201, json={"unexpected": "private-response"})
            return httpx.Response(201, json={"id": "dpl_Isolated01"})
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


def fixture(tmp_path, monkeypatch):
    def export(bundle, wheel):
        bundle.mkdir(parents=True)
        for name in (
            "app.py",
            "vercel.json",
            "runtime-requirements.txt",
            ".python-version",
            ".vercelignore",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            "oshepherd-manifest.json",
        ):
            (bundle / name).write_text("verified fixture export")
        (bundle / "vendor").mkdir()
        (bundle / "vendor" / wheel.name).write_bytes(wheel.read_bytes())

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
    deployment = VercelDeployment(target, config, directory, client=client)
    return cloud, client, deployment, wheel, state


@pytest.mark.parametrize("fault", ["lose_ack", "failed_ack", "fail_environment"])
def test_rest_failure_reconciles_scoped_encrypted_configuration_and_one_deployment(
    tmp_path, monkeypatch, fault
):
    async def run():
        cloud, client, adapter, wheel, state = fixture(tmp_path, monkeypatch)
        setattr(cloud, fault, True)
        async with client:
            with pytest.raises(
                OperatorError, match="vercel_(write_outcome_ambiguous|provider_failed)"
            ) as failure:
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
            assert not any(call.method == "POST" for call in cloud.calls)
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
            assert not any(call.method == "POST" for call in cloud.calls)

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


def test_malformed_create_ack_stays_pending_then_reconciles_without_duplicate(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        cloud.malformed_ack = True
        async with client:
            with pytest.raises(OperatorError, match="invalid_vercel_deployment_id"):
                await adapter.deploy(wheel)
            assert read_private_json(adapter.receipt_file)["creating"] is True
            assert (await adapter.deploy(wheel))["gateway_verified"]
            assert cloud.deploys == 1

    asyncio.run(run())


def test_environment_response_must_confirm_production_only_before_build(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        cloud.corrupt_environment = True
        async with client:
            with pytest.raises(OperatorError, match="vercel_environment_not_verified"):
                await adapter.deploy(wheel)
            assert cloud.deploys == 0

    asyncio.run(run())


def test_no_manual_wheel_or_cli_required_for_verified_installed_distribution(
    tmp_path, monkeypatch
):
    async def run():
        cloud, client, adapter, wheel, _ = fixture(tmp_path, monkeypatch)
        builds = []

        def materialize(directory):
            builds.append(directory)
            return wheel

        monkeypatch.setattr(
            "oshepherd.backends.supabase.vercel_deployment.materialize_current_wheel",
            materialize,
        )
        async with client:
            assert (await adapter.deploy())["gateway_verified"]
            assert builds == [adapter.directory / "runtime-wheel"]
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
