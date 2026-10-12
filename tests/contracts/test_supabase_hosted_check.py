"""Failure paths of the bounded hosted probe; no provider services required."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from scripts.supabase_hosted_check import check_lifetimes


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
