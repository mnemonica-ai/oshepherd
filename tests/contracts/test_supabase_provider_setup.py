"""Existing-project onboarding never adopts strangers or duplicates an uncertain write."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
import click
from click.testing import CliRunner

from oshepherd.backends.supabase.management import SupabaseManagement
from oshepherd.backends.supabase.operator import Installation, OperatorConfig
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.provider_setup import (
    check_new_edge_namespace,
    discover_projects,
    ensure_owner,
    prepare_existing_project,
)
from oshepherd.backends.supabase.releases import OperatorError
from oshepherd.backends.supabase import provider_cli

REF, ORG = "a" * 20, "selected-org"
PUBLIC, SECRET = "sb_publishable_" + "p" * 32, "sb_secret_" + "s" * 32
DATABASE = f"postgresql://postgres:private-password@db.{REF}.supabase.co/postgres"


def config():
    return OperatorConfig(
        project_ref=REF,
        supabase_url=f"https://{REF}.supabase.co",
        publishable_key=PUBLIC,
        secret_key=SECRET,
        database_url=DATABASE,
        exposed_schemas=["public", "existing_api"],
    )


class Provider:
    def __init__(self):
        self.requests = []
        self.organization = ORG
        self.functions = []
        self.secrets = []
        self.schemas = "public,existing_api"

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer private-token"
        if request.url.path == "/v1/organizations":
            return httpx.Response(200, json=[{"id": ORG, "name": "My Free org"}])
        if request.url.path == "/v1/projects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": REF,
                        "organization_id": self.organization,
                        "name": "Selected",
                        "region": "sa-east-1",
                        "status": "ACTIVE_HEALTHY",
                    }
                ],
            )
        if request.url.path.endswith("/postgrest"):
            return httpx.Response(200, json={"db_schema": self.schemas})
        if request.url.path.endswith("/api-keys"):
            return httpx.Response(
                200,
                json=[
                    {"name": "default", "type": "publishable", "api_key": PUBLIC},
                    {"name": "default", "type": "secret", "api_key": SECRET},
                ],
            )
        if request.url.path.endswith("/functions"):
            return httpx.Response(200, json=self.functions)
        if request.url.path.endswith("/secrets"):
            return httpx.Response(200, json=self.secrets)
        raise AssertionError("Unexpected request")


def test_existing_project_discovers_and_persists_without_mutations(tmp_path):
    async def run():
        transport = Provider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            projects = await discover_projects(provider)
            assert projects[0]["organization_name"] == "My Free org"
            result = await prepare_existing_project(
                provider, tmp_path, REF, ORG, DATABASE
            )
            assert result.exposed_schemas == ["public", "existing_api"]
            assert result == await prepare_existing_project(
                provider, tmp_path, REF, ORG, DATABASE
            )
            assert OperatorConfig.load(tmp_path / "connection.json") == result
            assert read_private_json(tmp_path / "edge-provider.json") == {
                "organization_id": ORG,
                "access_token": "private-token",
            }
            assert (tmp_path / "connection.json").stat().st_mode & 0o777 == 0o600
            assert "private-password" not in repr(result)

    asyncio.run(run())


def test_wrong_database_project_is_rejected_before_provider_calls(tmp_path):
    async def run():
        transport = Provider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            with pytest.raises(OperatorError, match="database_target_mismatch"):
                await prepare_existing_project(
                    provider, tmp_path, "b" * 20, ORG, DATABASE
                )
            assert not transport.requests

    asyncio.run(run())


@pytest.mark.parametrize(
    "existing", ["connection.json", "edge-provider.json", "installation.json"]
)
def test_unowned_files_are_preserved(tmp_path, existing):
    original = {"unrelated": "private-value"}
    write_private_json(tmp_path / existing, original)

    async def run():
        transport = Provider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            with pytest.raises(OperatorError, match="provider_setup_existing_state"):
                await prepare_existing_project(provider, tmp_path, REF, ORG, DATABASE)
            assert read_private_json(tmp_path / existing) == original
            assert not (tmp_path / "provider-setup.json").exists()

    asyncio.run(run())


def test_changed_selection_and_private_configuration_are_not_overwritten(tmp_path):
    async def run():
        transport = Provider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            await prepare_existing_project(provider, tmp_path, REF, ORG, DATABASE)
            original = (tmp_path / "connection.json").read_bytes()
            with pytest.raises(
                OperatorError, match="provider_setup_configuration_changed"
            ):
                await prepare_existing_project(
                    provider,
                    tmp_path,
                    REF,
                    ORG,
                    DATABASE.replace("private-password", "new-password"),
                )
            transport.organization = "another-org"
            with pytest.raises(OperatorError, match="provider_setup_target_mismatch"):
                await prepare_existing_project(
                    provider, tmp_path, REF, "another-org", DATABASE
                )
            assert (tmp_path / "connection.json").read_bytes() == original

    asyncio.run(run())


def test_resume_accepts_only_owned_schema_addition(tmp_path):
    async def run():
        transport = Provider()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            result = await prepare_existing_project(
                provider, tmp_path, REF, ORG, DATABASE
            )
            Installation(
                project_ref=REF,
                supabase_url=result.supabase_url,
                profile="edge_free",
                owner_subject=uuid4(),
            ).save(tmp_path)
            transport.schemas += ",oshepherd_api"
            assert (
                await prepare_existing_project(provider, tmp_path, REF, ORG, DATABASE)
                == result
            )
            transport.schemas += ",unrelated_new_api"
            with pytest.raises(
                OperatorError, match="provider_setup_configuration_changed"
            ):
                await prepare_existing_project(provider, tmp_path, REF, ORG, DATABASE)
            assert OperatorConfig.load(tmp_path / "connection.json") == result

    asyncio.run(run())


@pytest.mark.parametrize("collision", ["functions", "secrets"])
def test_existing_edge_assets_block_before_owner_creation(tmp_path, collision):
    async def run():
        transport = Provider()
        setattr(
            transport,
            collision,
            (
                [{"slug": "ollama"}]
                if collision == "functions"
                else [{"name": "OSHEPHERD_CLUSTER_ID"}]
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            provider = SupabaseManagement("private-token", client=client)
            with pytest.raises(OperatorError, match="edge_.*_collision"):
                await check_new_edge_namespace(provider, config(), tmp_path)
            assert not (tmp_path / "provider-owner.json").exists()

    asyncio.run(run())


class Auth:
    def __init__(self, directory):
        self.directory = directory
        self.user = None
        self.posts = 0
        self.lose_ack = False
        self.drop_write = False

    def handle(self, request):
        assert request.url.host == f"{REF}.supabase.co"
        if request.method == "POST":
            self.posts += 1
            journal = read_private_json(self.directory / "provider-owner.json")
            assert journal["stage"] == "creating"
            payload = json.loads(request.content)
            assert (
                payload["app_metadata"]["oshepherd_owner_operation"]
                == journal["operation_id"]
            )
            if not self.drop_write:
                self.user = {"id": str(uuid4()), **payload}
            if self.lose_ack:
                raise httpx.ReadTimeout("private-secret", request=request)
            return httpx.Response(200, json=self.user)
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json={"users": [self.user] if self.user else []})
        return httpx.Response(200, json=self.user)


def test_owner_creation_resumes_after_ack_loss_and_reuses_identity(tmp_path):
    async def run():
        auth = Auth(tmp_path)
        auth.lose_ack = True
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(auth.handle)
        ) as client:
            with pytest.raises(OperatorError, match="operator_auth_ambiguous") as error:
                await ensure_owner(config(), tmp_path, client=client)
            assert "private-secret" not in str(error.value)
            owner = await ensure_owner(config(), tmp_path, client=client)
            assert str(owner) == auth.user["id"]
            assert await ensure_owner(config(), tmp_path, client=client) == owner
            assert auth.posts == 1
            assert (
                read_private_json(tmp_path / "provider-owner.json")["stage"] == "ready"
            )

    asyncio.run(run())


def test_absent_ambiguous_owner_is_not_created_again(tmp_path):
    async def run():
        auth = Auth(tmp_path)
        auth.lose_ack = auth.drop_write = True
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(auth.handle)
        ) as client:
            with pytest.raises(OperatorError, match="operator_auth_ambiguous"):
                await ensure_owner(config(), tmp_path, client=client)
            with pytest.raises(
                OperatorError, match="provider_owner_creation_unresolved"
            ):
                await ensure_owner(config(), tmp_path, client=client)
            assert auth.posts == 1

    asyncio.run(run())


def test_owner_email_without_owned_metadata_is_not_adopted(tmp_path):
    async def run():
        auth = Auth(tmp_path)
        auth.lose_ack = True
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(auth.handle)
        ) as client:
            with pytest.raises(OperatorError):
                await ensure_owner(config(), tmp_path, client=client)
            auth.user["app_metadata"] = {}
            with pytest.raises(OperatorError, match="provider_owner_identity_conflict"):
                await ensure_owner(config(), tmp_path, client=client)
            assert auth.posts == 1

    asyncio.run(run())


def test_provider_failure_does_not_expose_response_secrets(tmp_path):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(403, json={"secret": SECRET})
            )
        ) as client:
            with pytest.raises(OperatorError, match="provider_access_denied") as error:
                await discover_projects(
                    SupabaseManagement("private-token", client=client)
                )
            assert SECRET not in str(error.value)

    asyncio.run(run())


def wizard_command(tmp_path):
    @click.command()
    def command():
        asyncio.run(provider_cli.guided_existing_setup(tmp_path))

    return command


def test_wizard_requires_psql_before_reading_credentials_or_cloud(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(provider_cli.shutil, "which", lambda _: None)
    result = CliRunner().invoke(wizard_command(tmp_path))
    assert isinstance(result.exception, OperatorError)
    assert str(result.exception) == "psql_required_for_project_setup"
    assert "access token" not in result.output


def test_wizard_decline_has_no_writes_or_echoed_secrets(monkeypatch, tmp_path):
    transport = Provider()
    monkeypatch.setattr(provider_cli.shutil, "which", lambda _: "/usr/bin/psql")

    def factory(token):
        assert token == "private-token"
        provider = SupabaseManagement(
            token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(transport.handle)),
        )
        provider.owns_client = True
        return provider

    monkeypatch.setattr(provider_cli, "SupabaseManagement", factory)
    result = CliRunner().invoke(
        wizard_command(tmp_path), input=f"private-token\n1\n{DATABASE}\nn\n"
    )
    assert result.exit_code == 1
    assert not list(tmp_path.iterdir())
    assert "private-token" not in result.output
    assert "private-password" not in result.output
    assert all(request.method == "GET" for request in transport.requests)


def test_wizard_refreshes_revoked_pat_only_for_same_bound_configuration(
    monkeypatch, tmp_path
):
    transport = Provider()

    async def prepare():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport.handle)
        ) as client:
            await prepare_existing_project(
                SupabaseManagement("private-token", client=client),
                tmp_path,
                REF,
                ORG,
                DATABASE,
            )

    asyncio.run(prepare())
    original = (tmp_path / "connection.json").read_bytes()

    def factory(token):
        def handle(request):
            if token == "private-token":
                return httpx.Response(403, json={"message": "private-revoked-detail"})
            assert token == "fresh-private-token"
            request.headers["Authorization"] = "Bearer private-token"
            return transport.handle(request)

        provider = SupabaseManagement(
            token, client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        )
        provider.owns_client = True
        return provider

    async def owner(*args, **kwargs):
        return uuid4()

    monkeypatch.setattr(provider_cli, "SupabaseManagement", factory)
    monkeypatch.setattr(provider_cli, "ensure_owner", owner)
    monkeypatch.setattr(provider_cli.shutil, "which", lambda _: "/usr/bin/psql")
    result = CliRunner().invoke(
        wizard_command(tmp_path), input="fresh-private-token\ny\n"
    )
    assert result.exit_code == 0, repr(result.exception)
    assert "fresh-private-token" not in result.output
    assert "private-revoked-detail" not in result.output
    assert (tmp_path / "connection.json").read_bytes() == original
    assert (
        read_private_json(tmp_path / "edge-provider.json")["access_token"]
        == "fresh-private-token"
    )
