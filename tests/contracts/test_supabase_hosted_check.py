"""Failure paths of the bounded hosted probe; no provider services required."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from scripts.supabase_hosted_check import check_lifetimes


@pytest.mark.parametrize(
    "mode", ["bounded_lifecycle", "soak", "auth_soak", "recovery", "envelope"]
)
def test_bounded_lifecycle_refuses_active_jobs_before_loading_worker_or_starting_upstream(
    monkeypatch,
    mode,
):
    import scripts.supabase_hosted_check as probe

    cluster = str(uuid4())
    queries = []
    monkeypatch.setattr(
        probe.OperatorConfig,
        "load",
        lambda _: SimpleNamespace(
            project_ref="a" * 20, profile="edge_free", gateway_url=None
        ),
    )
    monkeypatch.setattr(
        probe.Installation,
        "load",
        lambda *_: SimpleNamespace(cluster_id=cluster, stage="configured"),
    )

    class Database:
        def __init__(self, _):
            pass

        def execute(self, sql):
            queries.append(sql)
            return "1"

    def forbidden(*_, **__):
        pytest.fail("Active installation must not start a fake worker/upstream")

    monkeypatch.setattr(probe, "Database", Database)
    monkeypatch.setattr(probe.WorkerSession, "load", forbidden)
    monkeypatch.setattr(probe, "upstream", forbidden)
    with pytest.raises(
        ValueError, match="bounded_lifecycle_requires_idle_test_installation"
    ):
        asyncio.run(
            probe.run(
                SimpleNamespace(
                    connection_file="unused", state_dir="unused", **{mode: True}
                )
            )
        )
    assert len(queries) == 1 and "cluster_id='" + cluster + "'" in queries[0]
    assert queries[0].startswith("select count(*)")


def test_retired_installation_is_refused_before_auth_or_upstream(monkeypatch):
    import scripts.supabase_hosted_check as probe

    monkeypatch.setattr(
        probe.OperatorConfig,
        "load",
        lambda _: SimpleNamespace(project_ref="a" * 20, profile="edge_free"),
    )
    monkeypatch.setattr(
        probe.Installation, "load", lambda *_: SimpleNamespace(stage="disabled")
    )

    def forbidden(*_, **__):
        pytest.fail("retired installation must not access Auth/SQL or start upstream")

    monkeypatch.setattr(probe, "Database", forbidden)
    monkeypatch.setattr(probe.WorkerSession, "load", forbidden)
    monkeypatch.setattr(probe, "upstream", forbidden)
    with pytest.raises(
        ValueError, match="qualification_requires_configured_installation"
    ):
        asyncio.run(
            probe.run(SimpleNamespace(connection_file="unused", state_dir="unused"))
        )


def test_foreign_status_url_never_receives_application_key_and_owned_job_is_cancelled():
    async def run():
        identifier = str(uuid4())
        requests = []

        def handle(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    202,
                    json={
                        "id": identifier,
                        "status_url": "https://foreign.test/jobs/" + identifier,
                    },
                )
            assert request.method == "DELETE"
            return httpx.Response(200, json={"status": "cancelled"})

        results = {}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(AssertionError, match="unexpected_async_status_url"):
                await check_lifetimes(
                    client,
                    SimpleNamespace(overrides={}, requests=[]),
                    "https://gateway.test",
                    {"Authorization": "Bearer private-test-key"},
                    results,
                )
        assert all(request.url.host == "gateway.test" for request in requests)
        assert str(requests[-1].url) == "https://gateway.test/jobs/" + identifier
        assert results["async_cancel_acknowledged"] is True

    asyncio.run(run())


def test_cancel_ack_loss_preserves_failure_and_stops_the_concurrent_sync_request():
    async def run():
        identifier = str(uuid4())
        app = SimpleNamespace(overrides={}, requests=[])
        sync_started, sync_stopped = asyncio.Event(), asyncio.Event()
        results = {}

        async def handle(request):
            if request.method == "DELETE":
                raise httpx.ReadTimeout(
                    "simulated acknowledgement loss", request=request
                )
            if request.url.path == "/jobs":
                import json

                payload = json.loads(request.content)["payload"]
                app.requests.append({"path": "/api/generate", "json": payload})
                return httpx.Response(
                    202,
                    json={
                        "id": identifier,
                        "status_url": "https://gateway.test/jobs/" + identifier,
                    },
                )
            if request.url.path == "/api/generate":
                sync_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    sync_stopped.set()
            await sync_started.wait()
            return httpx.Response(200, json={"status": "failed"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(AssertionError, match="async_lifetime_failed"):
                async with asyncio.timeout(2):
                    await check_lifetimes(
                        client, app, "https://gateway.test", {}, results
                    )
        assert sync_stopped.is_set()
        assert results["async_status"] == "failed"
        assert results["async_cancel_acknowledged"] is False

    asyncio.run(run())


@pytest.mark.parametrize("expired_status", [401, 403, 200])
def test_auth_soak_requires_rejection_persisted_rotation_and_post_expiry_inference(
    tmp_path, monkeypatch, expired_status
):
    import json
    from pathlib import Path
    from pydantic import SecretStr
    import scripts.supabase_hosted_soak as soak
    from oshepherd.backends.supabase.sessions import WorkerSession

    async def run():
        url = "https://" + "a" * 20 + ".supabase.co"
        identifier = uuid4()
        original = WorkerSession(
            supabase_url=url,
            user_id=identifier,
            access_token="old",
            refresh_token="old-refresh",
            expires_at=1061,
        )
        rotated = WorkerSession(
            supabase_url=url,
            user_id=identifier,
            access_token="current",
            refresh_token="current-refresh",
            expires_at=4661,
        )
        filename = tmp_path / "private" / "session.json"
        rotated.save(filename)
        worker = SimpleNamespace(
            auth_session=original, rpc=SimpleNamespace(rpc=None), interval=1
        )
        worker_task = asyncio.create_task(asyncio.Event().wait())
        now = [1000]
        sleep = asyncio.sleep

        async def advance(seconds):
            if seconds == 5:
                now[0] = 1200
                worker.auth_session = rotated
                await sleep(0)
            else:
                await asyncio.Event().wait()

        monkeypatch.setattr(soak.time, "time", lambda: now[0])
        monkeypatch.setattr(soak.asyncio, "sleep", advance)
        fixture = next(
            item
            for item in json.loads(
                (
                    Path(__file__).resolve().parents[2]
                    / "protocol/v1/fixtures/routes.json"
                ).read_text()
            )["cases"]
            if item["id"] == "generate-defaults"
        )
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path == "/auth/v1/user":
                if request.headers["Authorization"] == "Bearer old":
                    return httpx.Response(
                        expired_status, json={"error_code": "bad_jwt"}
                    )
                return httpx.Response(200, json={"id": str(identifier)})
            return httpx.Response(
                200,
                json=(
                    fixture["response"]["json"]
                    if request.method == "POST"
                    else {"models": []}
                ),
            )

        results = {}
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                operation = soak.check_soak(
                    client,
                    worker,
                    worker_task,
                    SimpleNamespace(
                        supabase_url=url, publishable_key=SecretStr("publishable")
                    ),
                    None,
                    "https://gateway.test",
                    {},
                    filename,
                    None,
                    results,
                    load_jobs=0,
                )
                if expired_status == 200:
                    with pytest.raises(
                        AssertionError, match="original_auth_not_expired"
                    ):
                        await operation
                    assert not results["soak_complete"]
                    assert not any(request.method == "POST" for request in requests)
                else:
                    await operation
                    assert (
                        results["soak_complete"]
                        and results["refreshed_session_persisted"]
                    )
                    assert (
                        results["post_expiry_inference"]
                        and results["deterministic_jobs"] == 1
                    )
                    assert (
                        results["load_jobs_target"] == 0
                        and results["original_auth_status"] == expired_status
                    )
        finally:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

    asyncio.run(run())
