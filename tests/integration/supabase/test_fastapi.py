"""Optional gateway participates in real SQL/Auth and owned worker execution."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
import uvicorn

from oshepherd.api.factory import create_api_app
from oshepherd.backends.supabase.config import SupabaseGatewayConfig
from oshepherd.backends.supabase.storage import WorkerResultStorage
from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.worker.executor import OllamaExecutor
from tests.support.fake_ollama import Scenario
from .harness import queue_harness, scoped_sql
from .test_worker import upstream, running_worker

FIXTURES = Path(__file__).resolve().parents[3] / "protocol/v1/fixtures"


def gateway(h):
    return create_api_app(
        SupabaseGatewayConfig(
            SUPABASE_URL=h.connection["API_URL"],
            SUPABASE_SECRET_KEY=h.connection["SECRET_KEY"],
            SUPABASE_SCHEMA="oshepherd_spike_api",
            OSHEPHERD_CLUSTER_ID=h.cluster,
        ),
        backend="supabase",
    )


def test_fastapi_all_routes_and_private_async_result(local_connection):
    async def run():
        cases = json.loads((FIXTURES / "routes.json").read_text())["cases"]
        schema = json.loads(
            (FIXTURES.parents[1] / "supabase/v1/async.schema.json").read_text()
        )
        async with queue_harness(local_connection, workers=1) as h:
            async with running_worker(
                h, upstream(), result_storage=WorkerResultStorage(h.workers[0]["rpc"])
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway(h)),
                    base_url="http://gateway",
                    timeout=10,
                ) as client:
                    headers = {"Authorization": "Bearer " + h.keys[0]}
                    assert (await client.get("/health")).status_code == 401
                    assert (await client.get("/health", headers=headers)).json() == {
                        "status": 200
                    }
                    for name in (
                        "generate-defaults",
                        "chat-defaults",
                        "embeddings-defaults",
                        "systemone-text",
                        "systemone-extras-always-nonstream",
                        "generate-stream",
                        "chat-stream",
                    ):
                        case = next(item for item in cases if item["id"] == name)
                        response = await client.post(
                            case["request"]["path"],
                            json=case["request"]["json"],
                            headers=headers,
                        )
                        assert response.status_code == 200, (
                            name + ": " + str(response.status_code)
                        )
                        if "body_file" in case["response"]:
                            assert (
                                response.content
                                == (
                                    FIXTURES / case["response"]["body_file"]
                                ).read_bytes()
                            )
                        elif "json" in case["response"]:
                            assert response.json() == case["response"]["json"]
                        else:
                            assert response.text == case["response"]["body"]
                    for operation in ("tags", "ps", "version"):
                        assert (
                            await client.get("/api/" + operation, headers=headers)
                        ).status_code == 200
                    assert (
                        "model_info"
                        in (
                            await client.post(
                                "/api/show", json={"model": "fixture"}, headers=headers
                            )
                        ).json()
                    )
                    request = {
                        "endpoint": "/api/generate",
                        "payload": {"model": "fixture", "prompt": "async"},
                    }
                    accepted = await client.post(
                        "/jobs",
                        json=request,
                        headers={**headers, "Idempotency-Key": "fastapi-async"},
                    )
                    assert accepted.status_code == 202
                    url = accepted.json()["status_url"]
                    async with asyncio.timeout(10):
                        while True:
                            result = await client.get(url + "/result", headers=headers)
                            if result.status_code != 202:
                                break
                            await asyncio.sleep(0.1)
                    assert result.status_code == 200
                    status = await client.get(url, headers=headers)
                    Draft202012Validator(schema["$defs"]["status"]).validate(
                        status.json()
                    )
                    download = await client.get(
                        url + "/result?download=true", headers=headers
                    )
                    assert download.status_code == 200
                    descriptor = download.json()
                    Draft202012Validator(schema["$defs"]["download"]).validate(
                        descriptor
                    )
                    blob = await h.client.get(descriptor["url"])
                    assert (
                        hashlib.sha256(blob.content).hexdigest() == descriptor["sha256"]
                    )
                    assert blob.json() == result.json()
                    assert (
                        await client.get(
                            url, headers={"Authorization": "Bearer " + h.keys[1]}
                        )
                    ).status_code == 404

    asyncio.run(run())


@asynccontextmanager
async def socket_server(app, port):
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            lifespan="off",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Local test server did not start")
                await asyncio.sleep(0.02)
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)


def test_fastapi_socket_disconnect_cancels_real_job_and_upstream(local_connection):
    async def run():
        lines = (
            (FIXTURES / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)
        )
        app = upstream(
            {
                "/api/generate": Scenario(
                    tuple(lines), content_type="application/x-ndjson", gaps_s=(5, 5)
                )
            }
        )
        async with queue_harness(local_connection, workers=1) as h:
            executor = OllamaExecutor("http://127.0.0.1:55325")
            worker = SupabaseWorker(
                h.workers[0]["rpc"],
                executor,
                heartbeat_seconds=0.1,
                poll_seconds=0.05,
                drain_seconds=0,
            )
            stop = asyncio.Event()
            task = None
            try:
                async with socket_server(app, 55325):
                    task = asyncio.create_task(worker.run(stop))
                    await asyncio.wait_for(worker.ready.wait(), 5)
                    async with socket_server(gateway(h), 55326):
                        async with httpx.AsyncClient(timeout=5) as client:
                            async with client.stream(
                                "POST",
                                "http://127.0.0.1:55326/api/generate",
                                headers={"Authorization": "Bearer " + h.keys[0]},
                                json={
                                    "model": "fixture",
                                    "prompt": "disconnect",
                                    "stream": True,
                                },
                            ) as response:
                                assert response.status_code == 200
                                first = await response.aiter_lines().__anext__()
                                assert json.loads(first)["done"] is False
                            # This is an actual HTTP socket close, not an ASGI
                            # fake disconnect or direct generator cancellation.
                            async with asyncio.timeout(3):
                                while True:
                                    state = scoped_sql(
                                        h.cluster,
                                        "select state from oshepherd_spike.jobs where cluster_id = :'cluster_id'::uuid;",
                                    )
                                    if "cancelled" in state and not worker.jobs:
                                        break
                                    await asyncio.sleep(0.05)
                            assert not task.done()
            finally:
                stop.set()
                if task is not None:
                    await asyncio.wait_for(task, 5)
                await executor.close()

    asyncio.run(run())
