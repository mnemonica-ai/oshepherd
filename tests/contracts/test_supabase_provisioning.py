"""Mocked creation acceptance: private intent, consent, and uncertain write recovery."""

import asyncio
import json
from uuid import uuid4

import click
from click.testing import CliRunner
import httpx
import pytest

from oshepherd.backends.supabase.management import SupabaseManagement
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.provisioning import (
    available_regions,
    creation_record,
    execute_creation,
    organizations,
    plan_creation,
    wait_project,
)
from oshepherd.backends.supabase import provider_cli
from oshepherd.backends.supabase.operator import OperatorConfig
from oshepherd.backends.supabase.releases import OperatorError

ORG = {"id": "selected-org", "slug": "selected-org", "name": "Selected Free org"}
REF = "b" * 20
PUBLIC, SECRET = "sb_publishable_" + "p" * 32, "sb_secret_" + "s" * 32


class Cloud:
    def __init__(self, directory):
        self.directory = directory
        self.orgs = [ORG.copy()]
        self.projects = []
        self.posts = []
        self.requests = []
        self.lose_ack = None
        self.drop_write = False
        self.statuses = []
        self.invalid_regions = False
        self.plan = "free"

    def handle(self, request):
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer private-token"
        path = request.url.path
        if request.method == "POST":
            kind = "organization" if path == "/v1/organizations" else "project"
            record = creation_record(self.directory, kind)
            assert record["stage"] == "creating"
            assert (
                self.directory / (kind + "-create.json")
            ).stat().st_mode & 0o777 == 0o600
            payload = json.loads(request.content)
            assert payload["name"] == record["name"]
            self.posts.append((kind, payload))
            if kind == "organization":
                assert set(payload) == {"name"}
                value = {
                    "id": "created-org",
                    "slug": "created-org",
                    "name": payload["name"],
                }
                if not self.drop_write:
                    self.orgs.append(value)
            else:
                assert set(payload) == {
                    "name",
                    "db_pass",
                    "organization_slug",
                    "region_selection",
                }
                assert len(payload["db_pass"]) >= 32
                assert payload["region_selection"] == {
                    "type": "specific",
                    "code": "sa-east-1",
                }
                value = {
                    "id": REF,
                    "ref": REF,
                    "organization_id": payload["organization_slug"],
                    "organization_slug": payload["organization_slug"],
                    "name": payload["name"],
                    "region": "sa-east-1",
                    "status": "ACTIVE_HEALTHY",
                }
                if not self.drop_write:
                    self.projects.append(value)
            if self.lose_ack == kind:
                raise httpx.ReadTimeout("private provider response", request=request)
            return httpx.Response(201, json=value)
        assert request.method == "GET"
        if path == "/v1/organizations":
            return httpx.Response(200, json=self.orgs)
        if path.startswith("/v1/organizations/"):
            selected = next(
                item for item in self.orgs if item["slug"] == path.split("/")[-1]
            )
            return httpx.Response(
                200,
                json={
                    **selected,
                    **({"plan": self.plan} if self.plan is not None else {}),
                },
            )
        if path == "/v1/projects":
            if self.statuses:
                self.projects[0]["status"] = self.statuses.pop(0)
            return httpx.Response(200, json=self.projects)
        if path == "/v1/projects/available-regions":
            assert request.url.params["organization_slug"] in {
                item["slug"] for item in self.orgs
            }
            values = [
                {
                    "type": "specific",
                    "name": "São Paulo",
                    "code": "sa-east-1",
                    "provider": "AWS",
                },
                {
                    "type": "specific",
                    "name": "At capacity",
                    "code": "us-east-1",
                    "provider": "AWS",
                    "status": "capacity",
                },
            ]
            return httpx.Response(
                200, json={} if self.invalid_regions else {"all": {"specific": values}}
            )
        if path.endswith("/postgrest"):
            return httpx.Response(200, json={"db_schema": "public,graphql_public"})
        if path.endswith("/api-keys"):
            return httpx.Response(
                200,
                json=[
                    {"name": "default", "type": "publishable", "api_key": PUBLIC},
                    {"name": "default", "type": "secret", "api_key": SECRET},
                ],
            )
        if path.endswith("/functions") or path.endswith("/secrets"):
            return httpx.Response(200, json=[])
        raise AssertionError(path)


def provider(cloud):
    result = SupabaseManagement(
        "private-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(cloud.handle)),
    )
    result.owns_client = True
    return result


@pytest.mark.parametrize("kind", ["organization", "project"])
def test_create_is_journaled_and_repeated_execution_only_reads(tmp_path, kind):
    async def run():
        cloud = Cloud(tmp_path)
        management = provider(cloud)
        try:
            extra = (
                {"organization": ORG, "region": "sa-east-1"}
                if kind == "project"
                else {}
            )
            plan = await plan_creation(
                management, tmp_path, kind, "my-oshepherd", **extra
            )
            assert not cloud.posts
            assert plan["name"].startswith("my-oshepherd-os-")
            first = await execute_creation(management, tmp_path, kind)
            assert await execute_creation(management, tmp_path, kind) == first
            assert len(cloud.posts) == 1
            assert creation_record(tmp_path, kind)["stage"] == "created"
        finally:
            await management.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["organization", "project"])
def test_lost_ack_reconciles_without_duplicate_creation(tmp_path, kind):
    async def run():
        cloud = Cloud(tmp_path)
        cloud.lose_ack = kind
        management = provider(cloud)
        try:
            extra = (
                {"organization": ORG, "region": "sa-east-1"}
                if kind == "project"
                else {}
            )
            await plan_creation(management, tmp_path, kind, "oshepherd", **extra)
            with pytest.raises(OperatorError, match="provider_outcome_ambiguous"):
                await execute_creation(management, tmp_path, kind)
            recovered = await execute_creation(management, tmp_path, kind)
            assert recovered["id"]
            assert len(cloud.posts) == 1
        finally:
            await management.close()

    asyncio.run(run())


def test_absent_ambiguous_project_never_reposts(tmp_path):
    async def run():
        cloud = Cloud(tmp_path)
        cloud.lose_ack, cloud.drop_write = "project", True
        management = provider(cloud)
        try:
            await plan_creation(
                management,
                tmp_path,
                "project",
                "oshepherd",
                organization=ORG,
                region="sa-east-1",
            )
            with pytest.raises(OperatorError, match="provider_outcome_ambiguous"):
                await execute_creation(management, tmp_path, "project")
            for _ in range(2):
                with pytest.raises(OperatorError, match="creation_outcome_unresolved"):
                    await execute_creation(management, tmp_path, "project")
            assert len(cloud.posts) == 1
        finally:
            await management.close()

    asyncio.run(run())


def test_existing_name_is_not_adopted_and_selection_cannot_change(tmp_path):
    async def run():
        cloud = Cloud(tmp_path)
        management = provider(cloud)
        try:
            plan = await plan_creation(
                management, tmp_path, "organization", "oshepherd"
            )
            cloud.orgs.append(
                {"id": "foreign-org", "slug": "foreign-org", "name": plan["name"]}
            )
            with pytest.raises(OperatorError, match="creation_resource_collision"):
                await execute_creation(management, tmp_path, "organization")
            with pytest.raises(OperatorError, match="creation_selection_mismatch"):
                await plan_creation(management, tmp_path, "organization", "changed")
            assert not cloud.posts
        finally:
            await management.close()

    asyncio.run(run())


def test_uncertain_duplicate_and_wrong_region_are_not_adopted(tmp_path):
    async def run():
        cloud = Cloud(tmp_path)
        cloud.lose_ack = "project"
        management = provider(cloud)
        try:
            await plan_creation(
                management,
                tmp_path,
                "project",
                "oshepherd",
                organization=ORG,
                region="sa-east-1",
            )
            with pytest.raises(OperatorError):
                await execute_creation(management, tmp_path, "project")
            cloud.projects[0]["region"] = "us-east-1"
            with pytest.raises(OperatorError, match="creation_resource_collision"):
                await execute_creation(management, tmp_path, "project")
            cloud.projects[0]["region"] = "sa-east-1"
            cloud.projects.append({**cloud.projects[0], "id": "c" * 20})
            with pytest.raises(OperatorError, match="creation_resource_collision"):
                await execute_creation(management, tmp_path, "project")
            assert len(cloud.posts) == 1
        finally:
            await management.close()

    asyncio.run(run())


def test_regions_are_provider_discovered_and_fail_closed(tmp_path):
    async def run():
        cloud = Cloud(tmp_path)
        management = provider(cloud)
        try:
            assert await organizations(management) == [ORG]
            assert await available_regions(management, ORG) == [
                {"name": "São Paulo", "code": "sa-east-1"}
            ]
            with pytest.raises(OperatorError, match="project_region_unavailable"):
                await plan_creation(
                    management,
                    tmp_path,
                    "project",
                    "oshepherd",
                    organization=ORG,
                    region="us-east-1",
                )
            cloud.invalid_regions = True
            with pytest.raises(OperatorError, match="available_regions_unavailable"):
                await available_regions(management, ORG)
            assert not cloud.posts
        finally:
            await management.close()

    asyncio.run(run())


def test_health_wait_accepts_initial_inactive_and_is_bounded(tmp_path):
    async def run():
        cloud = Cloud(tmp_path)
        management = provider(cloud)
        try:
            await plan_creation(
                management,
                tmp_path,
                "project",
                "oshepherd",
                organization=ORG,
                region="sa-east-1",
            )
            await execute_creation(management, tmp_path, "project")
            cloud.statuses = ["INACTIVE", "COMING_UP", "ACTIVE_HEALTHY"]
            assert (
                await wait_project(management, REF, ORG["id"], timeout=1, interval=0)
            )["status"] == "ACTIVE_HEALTHY"
            cloud.projects[0]["status"] = "COMING_UP"
            with pytest.raises(OperatorError, match="project_readiness_timeout"):
                await wait_project(
                    management, REF, ORG["id"], timeout=0.01, interval=0.001
                )
            assert len(cloud.posts) == 1
        finally:
            await management.close()

    asyncio.run(run())


def wire_wizard(
    monkeypatch, cloud, directory, *, profile="edge_free", gateway_url=None
):
    def factory(token):
        assert token == "private-token"
        return provider(cloud)

    async def owner(config, target):
        assert target == directory
        assert config.database_url is None
        return uuid4()

    monkeypatch.setattr(provider_cli, "SupabaseManagement", factory)
    monkeypatch.setattr(provider_cli, "ensure_owner", owner)

    @click.command()
    def command():
        asyncio.run(
            provider_cli.guided_existing_setup(
                directory, profile=profile, gateway_url=gateway_url
            )
        )

    return command


@pytest.mark.parametrize("profile", ["edge_free", "vercel_hobby"])
def test_new_organization_project_wizard_needs_no_psql_or_database_input(
    monkeypatch, tmp_path, profile
):
    cloud = Cloud(tmp_path)
    gateway = "https://selected.vercel.app" if profile == "vercel_hobby" else None
    command = wire_wizard(
        monkeypatch, cloud, tmp_path, profile=profile, gateway_url=gateway
    )
    result = CliRunner().invoke(
        command, input="private-token\n0\n0\nmy-org\ny\ny\n1\nmy-project\ny\ny\ny\n"
    )
    assert result.exit_code == 0, repr(result.exception)
    assert [kind for kind, _ in cloud.posts] == ["organization", "project"]
    configuration = OperatorConfig.load(tmp_path / "connection.json")
    assert configuration.database_url is None
    assert configuration.profile == profile
    assert configuration.gateway_url == gateway
    assert "private-token" not in result.output
    assert "Postgres connection" not in result.output
    assert cloud.posts[1][1]["db_pass"] not in result.output
    if profile == "vercel_hobby":
        assert not any(
            request.url.path.endswith(("/functions", "/secrets"))
            for request in cloud.requests
        )
    assert not (tmp_path / "supabase-setup-token.json").exists()


def test_declined_project_creation_preserves_intent_without_dispatch(
    monkeypatch, tmp_path
):
    cloud = Cloud(tmp_path)
    command = wire_wizard(monkeypatch, cloud, tmp_path)
    result = CliRunner().invoke(
        command, input="private-token\n0\n1\ny\n1\nmy-project\nn\n"
    )
    assert result.exit_code == 1
    assert not cloud.posts
    assert creation_record(tmp_path, "project")["stage"] == "prepared"


def test_wizard_resumes_uncertain_project_using_same_private_journal(
    monkeypatch, tmp_path
):
    cloud = Cloud(tmp_path)
    cloud.lose_ack = "project"
    command = wire_wizard(monkeypatch, cloud, tmp_path)
    first = CliRunner().invoke(
        command, input="private-token\n0\n1\ny\n1\nmy-project\ny\n"
    )
    assert isinstance(first.exception, OperatorError)
    assert len(cloud.posts) == 1
    result = CliRunner().invoke(command, input="y\ny\ny\ny\n")
    assert result.exit_code == 0, repr(result.exception)
    assert len(cloud.posts) == 1
    assert "personal access token" not in result.output


@pytest.mark.parametrize("plan", ["pro", "team", "enterprise", "platform", "unknown"])
def test_nonfree_or_unrecognized_plan_blocks_project_write(tmp_path, plan):
    async def run():
        cloud = Cloud(tmp_path)
        management = provider(cloud)
        try:
            await plan_creation(
                management,
                tmp_path,
                "project",
                "oshepherd",
                organization=ORG,
                region="sa-east-1",
                free_attested=True,
            )
            cloud.plan = plan
            with pytest.raises(OperatorError, match="free_organization_required"):
                await execute_creation(management, tmp_path, "project")
            assert not cloud.posts
            assert creation_record(tmp_path, "project")["stage"] == "prepared"
        finally:
            await management.close()

    asyncio.run(run())


@pytest.mark.parametrize("attested", [True, False])
def test_missing_plan_requires_explicit_saved_attestation(tmp_path, attested):
    async def run():
        cloud = Cloud(tmp_path)
        cloud.plan = None
        management = provider(cloud)
        try:
            await plan_creation(
                management,
                tmp_path,
                "project",
                "oshepherd",
                organization=ORG,
                region="sa-east-1",
                free_attested=attested,
            )
            if attested:
                assert (await execute_creation(management, tmp_path, "project"))[
                    "id"
                ] == REF
            else:
                with pytest.raises(
                    OperatorError, match="free_organization_attestation_required"
                ):
                    await execute_creation(management, tmp_path, "project")
                assert not cloud.posts
        finally:
            await management.close()

    asyncio.run(run())


def test_existing_project_paid_organization_is_rejected_before_configuration(
    monkeypatch, tmp_path
):
    cloud = Cloud(tmp_path)
    cloud.projects = [
        {
            "id": REF,
            "organization_id": ORG["id"],
            "name": "Existing",
            "region": "sa-east-1",
            "status": "ACTIVE_HEALTHY",
        }
    ]
    cloud.plan = "pro"
    command = wire_wizard(monkeypatch, cloud, tmp_path)
    result = CliRunner().invoke(command, input="private-token\n1\n")
    assert isinstance(result.exception, OperatorError)
    assert str(result.exception) == "free_organization_required"
    assert not cloud.posts
    assert not (tmp_path / "connection.json").exists()


def test_wizard_missing_plan_requests_attestation_without_claiming_api_verification(
    monkeypatch, tmp_path
):
    cloud = Cloud(tmp_path)
    cloud.plan = None
    command = wire_wizard(monkeypatch, cloud, tmp_path)
    result = CliRunner().invoke(
        command, input="private-token\n0\n1\ny\n1\nmy-project\ny\ny\ny\n"
    )
    assert result.exit_code == 0, repr(result.exception)
    assert "optional billing plan field" in result.output
    assert "Supabase confirms" not in result.output
    assert creation_record(tmp_path, "project")["free_attested"] is True
