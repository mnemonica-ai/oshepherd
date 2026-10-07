"""Cleanup rejects unsafe paths and preserves intent after HTTP ambiguity."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from oshepherd.backends.supabase.client import RpcClient, RpcError
from oshepherd.backends.supabase.config import SupabaseMaintenanceConfig
from oshepherd.backends.supabase.maintenance import ResultMaintenance


@pytest.mark.parametrize(
    "unsafe", ["other_cluster", "traversal", "duplicate", "oversized"]
)
def test_invalid_cleanup_page_never_reaches_storage(unsafe):
    async def run():
        cluster = str(uuid4())
        path = f"{cluster}/{uuid4()}/{uuid4()}/result.json"
        paths = {
            "other_cluster": [f"{uuid4()}/{uuid4()}/{uuid4()}/result.json"],
            "traversal": [f"{cluster}/../result.json"],
            "duplicate": [path, path],
            "oversized": [path] * 3,
        }[unsafe]
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "v": 1,
                    "data": {
                        "claim_id": str(uuid4()),
                        "object_paths": paths,
                        "reaped": 0,
                        "purged": 0,
                        "scanned": 0,
                        "enqueued": 0,
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rpc = RpcClient("https://project.test", "sb_secret_test", client=client)
            with pytest.raises(RpcError):
                await ResultMaintenance(rpc, cluster, batch=2).cycle()
        assert calls == ["/rest/v1/rpc/maintenance_tick"]

    asyncio.run(run())


def test_lost_storage_delete_ack_reports_failure_then_recovers_absent_object():
    async def run():
        cluster = str(uuid4())
        path = f"{cluster}/{uuid4()}/{uuid4()}/result.json"
        absent = False
        acknowledgements = []

        def handler(request):
            nonlocal absent
            assert request.headers["apikey"] == "sb_secret_test"
            if request.method == "DELETE":
                assert request.url.path == "/storage/v1/object/oshepherd-spike-results"
                assert json.loads(request.content) == {"prefixes": [path]}
                if not absent:
                    absent = True
                    raise httpx.ReadTimeout("Object deleted; response lost")
                return httpx.Response(404, json={"error": "missing"})
            payload = json.loads(request.content)["p_request"]
            if request.url.path.endswith("/maintenance_tick"):
                data = {
                    "claim_id": str(uuid4()),
                    "object_paths": [path],
                    "reaped": 0,
                    "purged": 0,
                    "scanned": 1,
                    "enqueued": 0,
                }
            else:
                acknowledgements.append(payload)
                data = {
                    "deleted": len(payload["deleted_paths"]),
                    "retrying": len(payload["failed_paths"]),
                }
            return httpx.Response(200, json={"v": 1, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = ResultMaintenance(
                RpcClient(
                    "https://project.test",
                    "sb_secret_test",
                    schema="oshepherd_spike_api",
                    client=client,
                ),
                cluster,
            )
            first = await runner.cycle()
            second = await runner.cycle()
        assert first["deleted"] == 0 and first["retrying"] == 1
        assert second["deleted"] == 1 and second["retrying"] == 0
        assert acknowledgements[0]["failed_paths"] == [path]
        assert acknowledgements[1]["deleted_paths"] == [path]

    asyncio.run(run())


def test_maintenance_requires_admin_credential_and_finite_budgets():
    fields = {"SUPABASE_URL": "https://project.test", "OSHEPHERD_CLUSTER_ID": uuid4()}
    config = SupabaseMaintenanceConfig(**fields, SUPABASE_SECRET_KEY="sb_secret_test")
    assert "sb_secret_test" not in repr(config)
    for values in (
        {"SUPABASE_SECRET_KEY": "sb_publishable_test"},
        {"MAINTENANCE_BATCH": 65},
        {"MAINTENANCE_SECONDS": 1},
    ):
        with pytest.raises(ValidationError):
            SupabaseMaintenanceConfig(
                **{**fields, "SUPABASE_SECRET_KEY": "sb_secret_test", **values}
            )


def test_stopped_maintenance_never_starts_a_cycle():
    async def run():
        stop = asyncio.Event()
        stop.set()

        # No client or network access is needed when already stopped.
        class NeverCalled:
            schema = "oshepherd_api"

        await ResultMaintenance(NeverCalled(), uuid4()).run(stop)

    asyncio.run(run())
