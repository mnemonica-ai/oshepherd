"""Failure paths of the bounded hosted probe; no provider services required."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from scripts.supabase_hosted_check import check_lifetimes


def test_bounded_lifecycle_refuses_active_jobs_before_loading_worker_or_starting_upstream(
    monkeypatch,
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
        probe.Installation, "load", lambda *_: SimpleNamespace(cluster_id=cluster)
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
                    connection_file="unused", state_dir="unused", bounded_lifecycle=True
                )
            )
        )
    assert len(queries) == 1 and "cluster_id='" + cluster + "'" in queries[0]
    assert queries[0].startswith("select count(*)")


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
