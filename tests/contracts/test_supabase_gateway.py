import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest

from oshepherd.backends.supabase.config import SupabaseGatewayConfig
from oshepherd.backends.supabase.gateway import create_supabase_app
from oshepherd.backends.supabase.client import RpcError

FIXTURES = Path(__file__).resolve().parents[2] / "protocol/v1/fixtures"
KEY = "osk_" + "a" * 43


def config(**extra):
    return SupabaseGatewayConfig(
        **{
            "SUPABASE_URL": "https://project.test",
            "SUPABASE_SECRET_KEY": "sb_secret_gateway-test",
            "OSHEPHERD_CLUSTER_ID": uuid4(),
            **extra,
        }
    )


class FixtureRpc:
    schema = "oshepherd_spike_api"
    url = "https://project.test"

    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.closed = False
        self.job_id = str(uuid4())

    async def close(self):
        self.closed = True

    async def rpc(self, name, request):
        self.calls.append((name, request))
        if name == "backend_info":
            return {"protocol": 1}
        if name == "read_metadata":
            return {"response": self.fixture["response"]["json"]}
        if name == "submit_job":
            assert request["request"] == self.fixture["normalized"]
            return {"job_id": self.job_id, "state": "queued"}
        if name == "read_updates":
            if "body_file" in self.fixture["response"]:
                content = (
                    FIXTURES / self.fixture["response"]["body_file"]
                ).read_bytes()
                chunk = {
                    "seq": 1,
                    "content": content.decode(),
                    "checksum": hashlib.sha256(content).hexdigest(),
                }
                result = None
                chunks = [chunk] if request["cursors"][0]["cursor"] == 0 else []
                last_seq = 1
            else:
                content = json.dumps(self.fixture["response"]["json"]).encode()
                result = {
                    "inline": content.decode(),
                    "size_bytes": len(content),
                    "checksum": hashlib.sha256(content).hexdigest(),
                }
                chunks, last_seq = [], 0
            return {
                "jobs": [
                    {
                        "job_id": self.job_id,
                        "state": "succeeded",
                        "content_available": True,
                        "result": result,
                        "chunks": chunks,
                        "last_seq": last_seq,
                    }
                ]
            }
        if name == "cancel_job":
            return {"job_id": self.job_id, "state": "cancelled"}
        raise AssertionError("Unexpected RPC")


CASES = json.loads((FIXTURES / "routes.json").read_text())["cases"]
SUCCESS = [
    next(case for case in CASES if case["id"] == name)
    for name in (
        "generate-defaults",
        "chat-defaults",
        "embeddings-defaults",
        "systemone-text",
        "systemone-extras-always-nonstream",
        "generate-stream",
        "chat-stream",
    )
]


@pytest.mark.parametrize("fixture", SUCCESS, ids=lambda case: case["id"])
def test_fastapi_supabase_matches_success_and_exact_ndjson_fixtures(fixture):
    async def run():
        rpc = FixtureRpc(fixture)
        app = create_supabase_app(config(), rpc_factory=lambda: rpc)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            response = await client.post(
                fixture["request"]["path"],
                json=fixture["request"]["json"],
                headers={"Authorization": "Bearer " + KEY},
            )
        assert response.status_code == 200
        if "body_file" in fixture["response"]:
            assert (
                response.content
                == (FIXTURES / fixture["response"]["body_file"]).read_bytes()
            )
        else:
            assert response.json() == fixture["response"]["json"]
        assert response.headers["cache-control"] == "private, no-store"
        assert rpc.closed
        assert not any(name == "cancel_job" for name, _ in rpc.calls)

    asyncio.run(run())


@pytest.mark.parametrize(
    "path",
    ["/health", "/api/tags", "/api/generate", "/jobs", "/jobs/invalid", "/missing"],
)
def test_all_fastapi_paths_authenticate_before_parsing(path):
    async def run():
        rpc = FixtureRpc(SUCCESS[0])
        app = create_supabase_app(config(), rpc_factory=lambda: rpc)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            response = await client.post(path, content=b"not json")
        assert response.status_code == 401
        assert rpc.calls == []
        assert rpc.closed

    asyncio.run(run())


def test_gateway_import_and_factory_acquire_no_backend_or_network_resources():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import httpx; "
            "httpx.AsyncClient = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network resource acquired')); "
            "from oshepherd.api.factory import create_api_app; "
            "from oshepherd.backends.supabase.config import SupabaseGatewayConfig; "
            "app = create_api_app(SupabaseGatewayConfig(SUPABASE_URL='https://project.test', SUPABASE_SECRET_KEY='sb_secret_test', OSHEPHERD_CLUSTER_ID='00000000-0000-4000-8000-000000000001'), backend='supabase'); "
            "assert not any(n == 'redis' or n == 'celery' or n.startswith(('redis.', 'celery.')) for n in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_gateway_rejects_worker_credentials_and_invalid_hosting_budgets():
    with pytest.raises(ValueError):
        config(SUPABASE_SECRET_KEY="sb_publishable_test")
    with pytest.raises(ValueError):
        config(OSHEPHERD_GATEWAY_PROFILE="vercel", OSHEPHERD_RESPONSE_BYTES=4000001)
    with pytest.raises(ValueError):
        config(OSHEPHERD_SYNC_SECONDS=136)


def test_stream_socket_failure_cancels_sql_job_and_closes_request_client():
    from starlette.requests import ClientDisconnect

    async def run():
        rpc = FixtureRpc(SUCCESS[-2])
        app = create_supabase_app(config(), rpc_factory=lambda: rpc)
        body = json.dumps(rpc.fixture["request"]["json"]).encode()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body}
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("Client closed the socket")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/generate",
            "raw_path": b"/api/generate",
            "query_string": b"",
            "headers": [(b"authorization", ("Bearer " + KEY).encode())],
            "server": ("gateway", 80),
            "client": ("client", 1),
        }
        with pytest.raises(ClientDisconnect):
            await app(scope, receive, send)
        assert rpc.closed
        assert [name for name, _ in rpc.calls].count("cancel_job") == 1

    asyncio.run(run())


def test_vercel_import_requires_profile_and_disables_previews_by_default(monkeypatch):
    from oshepherd.api.factory import create_vercel_app

    monkeypatch.setenv("OSHEPHERD_BACKEND", "supabase")
    monkeypatch.setenv("OSHEPHERD_GATEWAY_PROFILE", "vercel")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.delenv("OSHEPHERD_ALLOW_PREVIEW_INFERENCE", raising=False)
    with pytest.raises(ValueError, match="Preview inference is disabled"):
        create_vercel_app()
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("OSHEPHERD_GATEWAY_PROFILE", "persistent")
    with pytest.raises(ValueError, match="vercel gateway profile"):
        create_vercel_app()


def test_vercel_invalid_environment_never_echoes_its_secret(monkeypatch):
    from oshepherd.api.factory import create_vercel_app

    monkeypatch.setenv("OSHEPHERD_BACKEND", "supabase")
    monkeypatch.setenv("OSHEPHERD_GATEWAY_PROFILE", "vercel")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sensitive-invalid-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.test")
    monkeypatch.setenv("OSHEPHERD_CLUSTER_ID", str(uuid4()))
    with pytest.raises(ValueError) as error:
        create_vercel_app()
    assert "sensitive-invalid-key" not in str(error.value)
