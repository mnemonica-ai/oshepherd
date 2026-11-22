"""Owned provider resources, bounded uploads and ambiguous deployment recovery."""

import asyncio
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from oshepherd.backends.supabase.edge_deployment import EdgeDeployment
from oshepherd.backends.supabase.management import SupabaseManagement
from oshepherd.backends.supabase.operator import Installation, OperatorConfig
from oshepherd.backends.supabase.releases import OperatorError, release_assets

REF, ORG, BUNDLE = "a" * 20, "isolated-org", "d" * 64


class Cloud:
    def __init__(self):
        self.function = None
        self.secrets = {"UNRELATED": "unrelated-digest"}
        self.deploys = 0
        self.secret_writes = 0
        self.mutations = []
        self.lose_secret_ack = False
        self.lose_deploy_ack = False
        self.disabled = False

    def handle(self, request):
        assert request.url.host == "api.supabase.com"
        assert request.headers["Authorization"] == "Bearer provider-token"
        path = request.url.path
        if request.method != "GET":
            self.mutations.append((request.method, path))
        if path == "/v1/projects":
            return httpx.Response(
                200,
                json=[{"id": REF, "organization_id": ORG, "status": "ACTIVE_HEALTHY"}],
            )
        if request.method == "GET":
            if path.endswith("/functions"):
                return httpx.Response(
                    200, json=[self.function] if self.function else []
                )
            if path.endswith("/secrets"):
                return httpx.Response(
                    200,
                    json=[
                        {"name": key, "value": value}
                        for key, value in self.secrets.items()
                    ],
                )
        if path.endswith("/functions/deploy"):
            message = BytesParser(policy=default).parsebytes(
                (
                    "Content-Type: " + request.headers["Content-Type"] + "\r\n\r\n"
                ).encode()
                + request.content
            )
            parts = list(message.iter_parts())
            metadata = json.loads(
                next(
                    part.get_payload(decode=True)
                    for part in parts
                    if part.get_param("name", header="content-disposition")
                    == "metadata"
                )
            )
            assert (
                metadata["entrypoint_path"] == "supabase/functions/ollama/index.ts"
                and metadata["verify_jwt"] is False
            )
            files = {
                part.get_filename(): part.get_payload(decode=True)
                for part in parts
                if part.get_filename()
            }
            root, manifest = release_assets()
            assert files == {
                "supabase/" + name: root.joinpath(name).read_bytes()
                for name in manifest["artifacts"]
                if name.startswith("functions/ollama/")
            }
            assert request.url.params["slug"] == "ollama"
            if request.url.params.get("bundleOnly") == "1":
                return httpx.Response(201, json={"ezbr_sha256": BUNDLE})
            self.deploys += 1
            self.function = {
                "id": str(uuid4()),
                "name": metadata["name"],
                "slug": "ollama",
                "status": "ACTIVE",
                "verify_jwt": False,
                "ezbr_sha256": BUNDLE,
            }
            if self.lose_deploy_ack:
                self.lose_deploy_ack = False
                raise httpx.ReadTimeout("ack lost", request=request)
            return httpx.Response(201, json=self.function)
        if request.method == "POST" and path.endswith("/secrets"):
            self.secret_writes += 1
            for item in json.loads(request.content):
                self.secrets[item["name"]] = hashlib.sha256(
                    item["value"].encode()
                ).hexdigest()
            if self.lose_secret_ack:
                self.lose_secret_ack = False
                raise httpx.ReadTimeout("ack lost", request=request)
            return httpx.Response(201, json=[])
        if request.method == "DELETE" and path.endswith("/functions/ollama"):
            assert self.disabled
            self.function = None
            return httpx.Response(200, json={})
        raise AssertionError("Unexpected provider operation")


def setup(tmp_path):
    config = OperatorConfig(
        project_ref=REF,
        supabase_url=f"https://{REF}.supabase.co",
        publishable_key="sb_publishable_" + "p" * 32,
        secret_key="sb_secret_" + "s" * 32,
    )
    state = Installation(
        project_ref=REF,
        supabase_url=config.supabase_url,
        profile="edge_free",
        owner_subject=uuid4(),
        stage="configured",
    )
    state.save(tmp_path)
    cloud = Cloud()
    provider_client = httpx.AsyncClient(transport=httpx.MockTransport(cloud.handle))
    provider = SupabaseManagement("provider-token", client=provider_client)

    def health(request):
        assert request.url.host == REF + ".supabase.co"
        assert "apikey" not in request.headers
        assert "provider-token" not in request.headers.get("Authorization", "")
        token = request.headers.get("Authorization")
        if token:
            assert token == "Bearer " + state.application_key.get_secret_value()
        return httpx.Response(
            200 if token else 401,
            json={"status": 200} if token else {"error": "unauthorized"},
        )

    gateway_client = httpx.AsyncClient(transport=httpx.MockTransport(health))
    deployment = EdgeDeployment(provider, config, tmp_path, ORG, client=gateway_client)
    return cloud, config, state, provider_client, gateway_client, deployment


@pytest.mark.parametrize("fault", ["lose_secret_ack", "lose_deploy_ack"])
def test_lost_ack_reconciles_owned_resource_without_duplicate_or_new_credentials(
    tmp_path, fault
):
    async def run():
        cloud, cfg, state, provider, gateway, deployment = setup(tmp_path)
        setattr(cloud, fault, True)
        async with provider, gateway:
            with pytest.raises(OperatorError, match="provider_outcome_ambiguous"):
                await deployment.deploy()
            result = await deployment.deploy()
            assert result["gateway_verified"] and not result["hosted_qualified"]
            identifier = cloud.function["id"]
            assert (await deployment.deploy())["function_id"] == identifier
            assert cloud.deploys == 1 and cloud.secret_writes == 1
            assert cloud.secrets["UNRELATED"] == "unrelated-digest"
            assert (
                state.application_key
                == Installation.load(tmp_path, cfg).application_key
            )
            assert deployment.receipt_file.stat().st_mode & 0o777 == 0o600
            assert (
                cfg.secret_key.get_secret_value()
                not in deployment.receipt_file.read_text()
            )

    asyncio.run(run())


def test_foreign_function_or_secret_collisions_refuse_mutation(tmp_path):
    async def run():
        cloud, _, _, provider, gateway, deployment = setup(tmp_path)
        async with provider, gateway:
            cloud.function = {"slug": "ollama", "id": "foreign", "name": "other-app"}
            with pytest.raises(OperatorError, match="edge_function_collision"):
                await deployment.deploy()
            assert not cloud.mutations
            cloud.function = None
            cloud.secrets["OSHEPHERD_CLUSTER_ID"] = "foreign-digest"
            with pytest.raises(OperatorError, match="edge_secret_collision"):
                await deployment.deploy()
            assert not cloud.mutations

    asyncio.run(run())


def test_adoption_requires_exact_artifact_cluster_configuration_and_health(tmp_path):
    async def run():
        cloud, _, state, provider, gateway, deployment = setup(tmp_path)
        async with provider, gateway:
            cloud.function = {
                "slug": "ollama",
                "id": "already-owned",
                "name": "ollama",
                "verify_jwt": False,
                "status": "ACTIVE",
                "ezbr_sha256": "e" * 64,
            }
            cloud.secrets.update(
                {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in deployment.values(state).items()
                }
            )
            with pytest.raises(OperatorError, match="edge_adoption_artifact_mismatch"):
                await deployment.deploy(adopt_existing=True)
            assert cloud.deploys == 0 and cloud.secret_writes == 0
            cloud.function["ezbr_sha256"] = BUNDLE
            assert (await deployment.deploy(adopt_existing=True))[
                "function_id"
            ] == "already-owned"
            assert cloud.deploys == 0 and cloud.secret_writes == 0

    asyncio.run(run())


def test_removal_is_receipt_bound_disables_first_and_retains_shared_data(tmp_path):
    async def run():
        cloud, cfg, state, provider, gateway, deployment = setup(tmp_path)
        async with provider, gateway:
            await deployment.deploy()
            identifier = cloud.function["id"]

            async def disable():
                cloud.disabled = True
                state.stage = "disabled"
                state.save(tmp_path)

            operator = SimpleNamespace(
                directory=tmp_path.absolute(), config=cfg, disable=disable
            )
            with pytest.raises(OperatorError, match="edge_removal_target_mismatch"):
                await deployment.remove(expected_function_id="wrong", operator=operator)
            assert not cloud.disabled and cloud.function
            plan = await deployment.removal_plan()
            assert (
                plan["function_id"] == identifier
                and "shared_schedules" in plan["preserve"]
            )
            before = dict(cloud.secrets)
            assert (
                await deployment.remove(
                    expected_function_id=identifier, operator=operator
                )
            )["gateway_removed"]
            assert cloud.secrets == before
            assert not cloud.function
            assert (
                await deployment.remove(
                    expected_function_id=identifier, operator=operator
                )
            )["gateway_removed"]
            with pytest.raises(OperatorError, match="installation_not_configured"):
                await deployment.deploy()

    asyncio.run(run())


def test_provider_never_follows_redirect_to_receive_admin_credentials():
    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                307, headers={"Location": "https://foreign.test/leak"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), follow_redirects=True
        ) as client:
            provider = SupabaseManagement("provider-token", client=client)
            with pytest.raises(OperatorError, match="provider_request_failed"):
                await provider.projects()
        assert len(requests) == 1 and requests[0].url.host == "api.supabase.com"

    asyncio.run(run())
