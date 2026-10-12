"""Local control-service authority, OAuth ambiguity and shared-engine recovery."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
import httpx
import pytest

from oshepherd.backends.supabase.installer import (
    Installer,
    InstallerConfig,
    create_installer_app,
    digest,
)
from oshepherd.backends.supabase.installer_store import InstallerStore
from oshepherd.backends.supabase.management import SupabaseManagement
from oshepherd.backends.supabase.oauth import SupabaseOAuth
from oshepherd.backends.supabase.operator import Operator, Installation, OperatorConfig
from oshepherd.backends.supabase.releases import OperatorError

REF, ORG = "a" * 20, "isolated-free-org"
PUBLIC, ADMIN = "sb_publishable_" + "p" * 32, "sb_secret_" + "s" * 32
LOGIN = "operator-access-" + "x" * 32


def config(tmp_path, **changes):
    return InstallerConfig(
        origin="http://127.0.0.1:55430",
        operator_subject=uuid4(),
        login_secret=LOGIN,
        encryption_key=Fernet.generate_key().decode(),
        oauth_client_id="local-app",
        oauth_client_secret="local-client-secret",
        allowed_projects={REF: ORG},
        deploy_gateway=False,
        state_dir=tmp_path / "state",
        **changes,
    )


class Provider:
    def __init__(self):
        self.requests = []
        self.tokens = 0
        self.fail_exchange = False
        self.revoked = False

    def handle(self, request):
        self.requests.append(request)
        assert request.url.host == "api.supabase.com"
        if request.url.path == "/v1/oauth/token":
            self.tokens += 1
            assert (
                request.headers["Authorization"]
                == "Basic "
                + base64.b64encode(b"local-app:local-client-secret").decode()
            )
            values = parse_qs(request.content.decode())
            if self.fail_exchange:
                raise httpx.ReadTimeout("private acknowledgement loss", request=request)
            if values["grant_type"] == ["authorization_code"]:
                assert values["redirect_uri"] == [
                    "http://127.0.0.1:55430/oauth/callback"
                ]
                assert 43 <= len(values["code_verifier"][0]) <= 128
            return httpx.Response(
                200,
                json={
                    "access_token": "management-access",
                    "refresh_token": "management-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert request.headers["Authorization"] == "Bearer management-access"
        if self.revoked:
            return httpx.Response(403, json={"message": "private revoked detail"})
        if request.url.path == "/v1/projects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": ref,
                        "organization_id": org,
                        "name": "dedicated",
                        "region": "sa-east-1",
                        "status": "ACTIVE_HEALTHY",
                    }
                    for ref, org in ((REF, ORG), ("b" * 20, "other-org"))
                ],
            )
        if request.url.path.endswith("/postgrest"):
            return httpx.Response(
                200, json={"db_schema": "public,graphql_public,existing_api"}
            )
        if request.url.path.endswith("/api-keys"):
            assert request.url.params["reveal"] == "true"
            return httpx.Response(
                200,
                json=[
                    {"type": "publishable", "name": "default", "api_key": PUBLIC},
                    {"type": "secret", "name": "default", "api_key": ADMIN},
                    {"type": "legacy", "name": "anon", "api_key": "ignored-legacy"},
                ],
            )
        raise AssertionError("Unexpected provider mutation")


def fixture(tmp_path, *, clock=lambda: 1000, operator_factory=Operator):
    settings = config(tmp_path)
    provider = Provider()
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider.handle))
    oauth = SupabaseOAuth(
        "local-app",
        "local-client-secret",
        settings.origin + "/oauth/callback",
        client=client,
    )
    service = Installer(
        settings,
        oauth=oauth,
        clock=clock,
        management_factory=lambda token: SupabaseManagement(token, client=client),
        operator_factory=operator_factory,
    )
    return settings, provider, client, service


async def sign_in(browser):
    response = await browser.post("/login", json={"secret": LOGIN})
    assert response.status_code == 200
    browser.headers["X-CSRF-Token"] = response.json()["csrf"]


async def connect(browser):
    result = await browser.post("/oauth/start")
    assert result.status_code == 200
    parameters = parse_qs(urlsplit(result.json()["authorize_url"]).query)
    response = await browser.get(
        "/oauth/callback",
        params={
            "state": parameters["state"][0],
            "code": "one-use-code",
        },
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    return parameters


def browser(settings, service):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_installer_app(settings, installer=service)
        ),
        base_url=settings.origin,
        headers={"Origin": settings.origin},
    )


def test_local_oauth_pkce_encrypted_tokens_session_binding_and_single_use(tmp_path):
    async def run():
        settings, provider, client, service = fixture(tmp_path)
        async with (
            client,
            browser(settings, service) as first,
            browser(settings, service) as second,
        ):
            assert (await first.get("/projects")).status_code == 401
            await sign_in(first)
            await sign_in(second)
            start = await first.post("/oauth/start")
            params = parse_qs(urlsplit(start.json()["authorize_url"]).query)
            assert params["code_challenge_method"] == ["S256"] and "scope" not in params
            state = params["state"][0]
            flow = service.store.load("flow-" + digest(state))
            expected = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(flow["verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert params["code_challenge"] == [expected]
            query = {"state": state, "code": "one-use-code"}
            assert (await second.get("/oauth/callback", params=query)).json()[
                "error"
            ] == "oauth_session_mismatch"
            assert provider.tokens == 0
            assert (await first.get("/oauth/callback", params=query)).status_code == 303
            assert (await first.get("/oauth/callback", params=query)).status_code == 401
            assert provider.tokens == 1
            projects = (await first.get("/projects")).json()["projects"]
            assert [item["id"] for item in projects] == [REF]
            cookie = first.cookies["oshepherd_installer"]
            for path in service.store.directory.glob("*.json"):
                content = path.read_text()
                assert all(
                    secret not in content
                    for secret in (
                        "management-access",
                        "management-refresh",
                        flow["verifier"],
                        cookie,
                    )
                )
                assert path.stat().st_mode & 0o777 == 0o600
            assert (await first.delete("/oauth/grant")).json()[
                "provider_credentials_deleted"
            ]
            assert (await first.get("/projects")).status_code == 401

    asyncio.run(run())


def test_oauth_denial_expiry_and_lost_exchange_never_retry_code(tmp_path):
    async def run():
        now = [1000]
        settings, provider, client, service = fixture(tmp_path, clock=lambda: now[0])
        async with client, browser(settings, service) as ui:
            await sign_in(ui)

            async def start():
                value = await ui.post("/oauth/start")
                return parse_qs(urlsplit(value.json()["authorize_url"]).query)["state"][
                    0
                ]

            denied = await start()
            assert (
                await ui.get(
                    "/oauth/callback",
                    params={"state": denied, "error": "access_denied"},
                )
            ).json()["error"] == "oauth_consent_denied"
            assert provider.tokens == 0
            expired = await start()
            now[0] += 301
            assert (
                await ui.get(
                    "/oauth/callback", params={"state": expired, "code": "private-code"}
                )
            ).status_code == 401
            ambiguous = await start()
            provider.fail_exchange = True
            response = await ui.get(
                "/oauth/callback", params={"state": ambiguous, "code": "private-code"}
            )
            assert response.json()["error"] == "oauth_exchange_ambiguous_reauthorize"
            assert "private" not in response.text
            await ui.get(
                "/oauth/callback", params={"state": ambiguous, "code": "private-code"}
            )
            assert provider.tokens == 1

    asyncio.run(run())


def test_mutations_require_origin_csrf_and_logout_invalidates_session(tmp_path):
    async def run():
        settings, _, client, service = fixture(tmp_path)
        async with client, browser(settings, service) as ui:
            await sign_in(ui)
            for headers in (
                {"Origin": "https://foreign.test"},
                {"X-CSRF-Token": "wrong"},
            ):
                response = await ui.post("/oauth/start", headers=headers)
                assert response.json()["error"] == "installer_csrf_failed"
            assert (
                await ui.get("/session", headers={"Host": "foreign.test"})
            ).status_code == 400
            assert (await ui.post("/logout")).status_code == 200
            assert (await ui.get("/session")).status_code == 401
            assert (await ui.post("/login", content=b"x" * 16385)).json()[
                "error"
            ] == "installer_request_too_large"

    asyncio.run(run())


def test_refresh_ambiguity_is_durable_and_reauthorization_required(tmp_path):
    async def run():
        settings, provider, client, service = fixture(tmp_path)
        async with client, browser(settings, service) as ui:
            await sign_in(ui)
            await connect(ui)
            with service.store.lock():
                grant = service.store.load(service.grant_key)
                grant["access_expires_at"] = 1000
                service.store.save(
                    service.grant_key, grant, expires_at=grant["retain_until"]
                )
            provider.fail_exchange = True
            assert (await ui.get("/projects")).json()[
                "error"
            ] == "oauth_exchange_ambiguous_reauthorize"
            assert (await ui.get("/projects")).json()[
                "error"
            ] == "oauth_refresh_ambiguous_reauthorize"
            assert provider.tokens == 2

    asyncio.run(run())


def test_project_authority_and_database_origin_checked_before_queue(tmp_path):
    async def run():
        settings, provider, client, service = fixture(tmp_path)
        async with client, browser(settings, service) as ui:
            await sign_in(ui)
            await connect(ui)
            values = {
                "project_ref": REF,
                "organization_id": ORG,
                "owner_subject": str(uuid4()),
                "database_url": "postgresql://postgres:private-password@foreign.test/postgres",
            }
            before = len(provider.requests)
            response = await ui.post(
                "/installations", json=values | {"organization_id": "wrong"}
            )
            assert response.json()["error"] == "installer_target_not_authorized"
            assert len(provider.requests) == before
            response = await ui.post("/installations", json=values)
            assert response.json()["error"] == "database_target_mismatch"
            assert "private-password" not in response.text
            assert not list(service.store.directory.glob("installation-*.json"))
            provider.revoked = True
            response = await ui.post("/installations", json=values)
            assert response.json()["error"] == "provider_access_denied_reauthorize"

    asyncio.run(run())


def test_browser_close_service_restart_and_partial_shared_engine_recovery(tmp_path):
    async def run():
        owner = uuid4()
        operations = []
        fail = [4]

        class Database:
            def migrate(self):
                return None

            def schedules(self):
                return None

        def runtime(request):
            if "/auth/v1/admin/users/" in request.url.path:
                return httpx.Response(200, json={"id": str(owner)})
            envelope = json.loads(request.content)["p_request"]
            name = request.url.path.rsplit("/", 1)[-1]
            operations.append((name, envelope))
            if name == "create_api_key" and fail[0]:
                fail[0] -= 1
                raise httpx.ReadTimeout("lost acknowledgement", request=request)
            data = (
                {"enabled": True, "owner_subject": str(owner)}
                if name == "installation_info"
                else {}
            )
            return httpx.Response(200, json={"v": 1, "data": data})

        runtime_client = httpx.AsyncClient(transport=httpx.MockTransport(runtime))

        class SharedOperator(Operator):
            async def install(self, subject, **_):
                return await super().install(subject, database=Database())

        factory = lambda cfg, directory: SharedOperator(
            cfg, directory, client=runtime_client
        )
        settings, provider, client, service = fixture(
            tmp_path, operator_factory=factory
        )
        async with client, runtime_client:
            async with browser(settings, service) as ui:
                await sign_in(ui)
                await connect(ui)
                selected = {
                    "project_ref": REF,
                    "organization_id": ORG,
                    "owner_subject": str(owner),
                    "database_url": f"postgresql://postgres:private-password@db.{REF}.supabase.co/postgres",
                }
                assert (
                    await ui.post("/installations", json=selected)
                ).status_code == 202
                assert not operations  # Browser only queued work.
            # A new service instance consumes durable encrypted work.
            service = Installer(
                settings,
                oauth=service.oauth,
                clock=lambda: 1000,
                management_factory=service.management_factory,
                operator_factory=factory,
            )
            result = await service.work_once()
            assert result["error"] == "installer_setup_failed"
            assert result["stage"] == "authorized"
            directory = settings.state_dir / "operators" / REF
            configuration = service.store.load("installation-" + REF)
            state = Installation.load(
                directory, OperatorConfig.load(directory / "connection.json")
            )
            original = state.application_key
            async with browser(settings, service) as ui:
                await sign_in(ui)
                assert (
                    await ui.post(
                        "/installations",
                        json=selected | {"owner_subject": str(uuid4())},
                    )
                ).json()["error"] == "installation_owner_mismatch"
                assert (
                    await ui.post(f"/installations/{REF}/resume")
                ).status_code == 202
                assert (await service.work_once())["stage"] == "configured"
                assert (await ui.delete("/oauth/grant")).status_code == 200
            assert "configuration" not in service.store.load("installation-" + REF)
            assert (
                Installation.load(
                    directory, OperatorConfig.load(directory / "connection.json")
                ).application_key
                == original
            )
            creates = [item for name, item in operations if name == "create_api_key"]
            assert len({item["operation_id"] for item in creates}) == 1
            assert len({item["raw_key"] for item in creates}) == 1
            assert not result["cloud_ready"] and not result["inference_verified"]

    asyncio.run(run())


def test_store_ciphertext_cannot_be_moved_to_another_record_or_read_without_key(
    tmp_path,
):
    store = InstallerStore(tmp_path / "vault", Fernet.generate_key())
    first, second = "flow-" + "a" * 64, "flow-" + "b" * 64
    with store.lock():
        store.save(first, {"secret": "provider-token"}, expires_at=253402300799)
        store.path(second).write_bytes(store.path(first).read_bytes())
        store.path(second).chmod(0o600)
        with pytest.raises(OperatorError, match="installer_record_mismatch"):
            store.load(second)
        with pytest.raises(OperatorError, match="installation_busy"):
            with store.lock():
                pass
    other = InstallerStore(store.directory, Fernet.generate_key())
    with pytest.raises(OperatorError, match="invalid_installer_record"):
        other.load(first)


def test_worker_downloads_are_project_bound_portable_and_contain_no_admin_credentials(
    tmp_path,
):
    from oshepherd.backends.supabase.operator import Enrollment
    from oshepherd.backends.supabase.private_files import write_private_json

    async def run():
        settings, _, client, service = fixture(tmp_path)
        directory = settings.state_dir / "operators" / REF
        owner = uuid4()
        cfg = OperatorConfig(
            project_ref=REF,
            supabase_url=f"https://{REF}.supabase.co",
            publishable_key=PUBLIC,
            secret_key=ADMIN,
        )
        write_private_json(
            directory / "connection.json",
            {
                "project_ref": REF,
                "supabase_url": cfg.supabase_url,
                "publishable_key": PUBLIC,
                "secret_key": ADMIN,
            },
        )
        state = Installation(
            project_ref=REF,
            supabase_url=cfg.supabase_url,
            profile="edge_free",
            owner_subject=owner,
            stage="configured",
            workers={
                "workstation": Enrollment(
                    capacity=2, auth_subject=uuid4(), worker_id=uuid4()
                )
            },
        )
        state.save(directory)
        write_private_json(
            directory / "workstation.enrollment.json",
            {
                "v": 1,
                "supabase_url": cfg.supabase_url,
                "token_hash": "one-use-fixture",
            },
        )
        with service.store.lock():
            service.store.save(
                "installation-" + REF,
                {
                    "project_ref": REF,
                    "organization_id": ORG,
                    "operator_subject": str(settings.operator_subject),
                    "owner_subject": str(owner),
                    "stage": "configured",
                    "queued": False,
                    "error": None,
                },
                expires_at=253402300799,
            )
        async with client, browser(settings, service) as ui:
            path = f"/installations/{REF}/workers/workstation/"
            assert (await ui.get(path + "environment")).status_code == 401
            await sign_in(ui)
            records = (await ui.get("/installations")).json()["installations"]
            assert len(records) == 1 and records[0]["project_ref"] == REF
            env = await ui.get(path + "environment")
            assert env.status_code == 200 and env.headers["Cache-Control"] == "no-store"
            assert ADMIN not in env.text and "SUPABASE_SECRET_KEY" not in env.text
            assert "management-" not in env.text and "one-use-fixture" not in env.text
            assert (
                PUBLIC in env.text
                and ".oshepherd/workers/workstation.worker-session.json" in env.text
            )
            assert str(directory) not in env.text
            challenge = await ui.get(path + "enrollment")
            assert challenge.json() == {
                "v": 1,
                "supabase_url": cfg.supabase_url,
                "token_hash": "one-use-fixture",
            }
            write_private_json(
                directory / "workstation.enrollment.json",
                {
                    "v": 1,
                    "supabase_url": "https://" + "b" * 20 + ".supabase.co",
                    "token_hash": "foreign",
                },
            )
            assert (await ui.get(path + "enrollment")).status_code == 400
            state.workers["workstation"].revocation_requested = True
            state.save(directory)
            assert (await ui.get(path + "environment")).json()[
                "error"
            ] == "worker_enrollment_unavailable"

    asyncio.run(run())


@pytest.mark.parametrize(
    "origin",
    [
        "http://remote.test",
        "https://installer.test/path",
        "https://user:password@installer.test",
        "https://installer.test?redirect=foreign",
    ],
)
def test_callback_origin_is_fixed_and_credentials_cannot_change_destination(
    tmp_path, origin
):
    values = config(tmp_path).model_dump()
    with pytest.raises(ValueError):
        InstallerConfig.model_validate(values | {"origin": origin})
