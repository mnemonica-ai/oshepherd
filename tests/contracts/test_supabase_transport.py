import asyncio
import json
import uuid

import httpx
import pytest

from oshepherd.backends.supabase.client import RpcClient, RpcError
from oshepherd.backends.supabase.config import SupabaseWorkerConfig
from oshepherd.backends.supabase.sessions import WorkerAuth, WorkerSession


def test_rpc_retry_replays_identical_operation_and_keeps_credentials_separate():
    async def run():
        requests = []

        async def handler(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout("acknowledgement lost", request=request)
            return httpx.Response(200, json={"v": 1, "data": {"job_id": "same-job"}})

        async def sleep(_):
            pass

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rpc = RpcClient(
                "https://project.test",
                "publishable-key",
                access_token="worker-jwt",
                client=client,
                schema="oshepherd_spike_api",
                sleep=sleep,
            )
            assert await rpc.rpc("submit_job", {"payload": {"model": "fixture"}}) == {
                "job_id": "same-job"
            }
        assert requests[0].content == requests[1].content
        envelope = json.loads(requests[0].content)["p_request"]
        assert envelope["v"] == 1
        uuid.UUID(envelope["operation_id"])
        assert requests[0].headers["apikey"] == "publishable-key"
        assert requests[0].headers["authorization"] == "Bearer worker-jwt"
        assert requests[0].headers["content-profile"] == "oshepherd_spike_api"

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        {"v": 2, "data": {}},
        {"v": True, "data": {}},
        {"v": 1.0, "data": {}},
        {"v": 1, "data": []},
        {"v": 1},
        [],
        "bad",
    ],
)
def test_incompatible_rpc_response_is_rejected(body):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        ) as client:
            with pytest.raises(RpcError, match="protocol_mismatch"):
                await RpcClient("https://project.test", "key", client=client).rpc(
                    "backend_info", {}
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "header,expected",
    [("1", 1), ("1000000", 2), ("-1", 0), ("NaN", None), ("bogus", None)],
)
def test_retry_after_is_finite_and_bounded(header, expected):
    from oshepherd.backends.supabase.client import bounded_retry_after

    assert bounded_retry_after(header) == expected


def test_backend_error_does_not_echo_sql_or_secrets():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    409,
                    json={
                        "message": "SQL failure containing secret-key and prompt text"
                    },
                )
            )
        ) as client:
            with pytest.raises(RpcError) as error:
                await RpcClient(
                    "https://project.test", "secret-key", client=client
                ).rpc("finish_job", {})
            assert str(error.value) == "backend_error"

    asyncio.run(run())


def test_worker_config_uses_only_its_fields_and_ollama_precedence():
    config = SupabaseWorkerConfig(
        SUPABASE_URL="http://127.0.0.1:55321",
        SUPABASE_PUBLISHABLE_KEY="key",
        OLLAMA_HOST="localhost:11435",
    )
    assert config.ollama_url == "http://localhost:11435"
    assert "CELERY_BROKER_URL" not in SupabaseWorkerConfig.model_fields
    assert "SUPABASE_SECRET_KEY" not in SupabaseWorkerConfig.model_fields
    assert (
        SupabaseWorkerConfig(
            **{**config.model_dump(), "OLLAMA_BASE_URL": "http://localhost:11436"}
        ).ollama_url
        == "http://localhost:11436"
    )
    with pytest.raises(ValueError):
        SupabaseWorkerConfig(**{**config.model_dump(), "PREFETCH_MULTIPLIER": 2})


@pytest.mark.parametrize(
    "url",
    [
        "http://project.test",
        "https://user:secret@project.test",
        "https://project.test?key=secret",
        "https://project.test/rest/v1",
    ],
)
def test_worker_config_rejects_unsafe_project_origins(url):
    with pytest.raises(ValueError):
        SupabaseWorkerConfig(SUPABASE_URL=url, SUPABASE_PUBLISHABLE_KEY="key")


def test_session_file_is_private_bound_to_project_and_atomic(tmp_path):
    path = tmp_path / "private" / "session.json"
    session = WorkerSession(
        supabase_url="https://project.test",
        user_id=uuid.uuid4(),
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=123,
    )
    session.save(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert (
        WorkerSession.load(path, supabase_url="https://project.test").user_id
        == session.user_id
    )
    with pytest.raises(ValueError, match="another project"):
        WorkerSession.load(path, supabase_url="https://other.test")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        WorkerSession.load(path, supabase_url="https://project.test")


def test_worker_configuration_rejects_administrative_key():
    with pytest.raises(ValueError, match="never an administrative key"):
        SupabaseWorkerConfig(
            SUPABASE_URL="https://project.test",
            SUPABASE_PUBLISHABLE_KEY="sb_secret_example",
        )


def test_session_paths_cannot_follow_symlinks(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    session = WorkerSession(
        supabase_url="https://project.test",
        user_id=uuid.uuid4(),
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=123,
    )
    with pytest.raises(ValueError, match="symlink"):
        session.save(link)
    assert target.read_text() == "keep"


def test_rpc_stops_oversized_responses_and_closes_the_socket():
    class Oversized(httpx.AsyncByteStream):
        closed = False
        fragments = 0

        async def __aiter__(self):
            for _ in range(100):
                self.fragments += 1
                yield b"x" * 65536

        async def aclose(self):
            self.closed = True

    async def run():
        body = Oversized()
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(RpcError, match="response_too_large"):
                await RpcClient("https://project.test", "key", client=client).rpc(
                    "read_updates", {}
                )
        assert body.closed
        assert body.fragments == 33
        assert len(requests) == 1

    asyncio.run(run())


def test_rpc_deadline_bounds_a_silent_response_and_closes_it():
    class Silent(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    async def run():
        body = Silent()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=body))
        ) as client:
            with pytest.raises(RpcError, match="backend_unavailable"):
                await asyncio.wait_for(
                    RpcClient("https://project.test", "key", client=client).rpc(
                        "backend_info", {}, attempts=1
                    ),
                    12,
                )
        assert body.closed

    asyncio.run(run())


def test_auth_does_not_replay_an_ambiguous_token_exchange():
    async def run():
        requests = []

        def lost(request):
            requests.append(request)
            raise httpx.RemoteProtocolError("Secret response lost")

        async with httpx.AsyncClient(transport=httpx.MockTransport(lost)) as client:
            with pytest.raises(RpcError, match="worker_auth_ambiguous"):
                await WorkerAuth(
                    "https://project.test", "publishable", client=client
                ).enroll("single-use-secret")
        assert len(requests) == 1

    asyncio.run(run())


@pytest.mark.parametrize("response", [{}, {"access_token": "secret"}, "invalid"])
def test_malformed_auth_response_has_a_safe_error(response):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
        ) as client:
            with pytest.raises(RpcError, match="invalid_auth_response"):
                await WorkerAuth(
                    "https://project.test", "publishable", client=client
                ).enroll("single-use-secret")

    asyncio.run(run())
