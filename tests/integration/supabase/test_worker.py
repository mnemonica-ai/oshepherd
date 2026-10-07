"""External async worker with real SQL/Auth and a deterministic HTTP upstream."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

import httpx
import pytest

from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.common.protocol import normalize_request
from oshepherd.worker.executor import OllamaExecutor
from tests.support.fake_ollama import FakeOllama, Scenario
from .harness import queue_harness, scoped_sql

FIXTURES = Path(__file__).resolve().parents[3] / "protocol/v1/fixtures"


def upstream(overrides=None):
    fixtures = json.loads((FIXTURES / "upstream.json").read_text())
    tags = fixtures["tags"]
    tags["models"][0]["digest"] = "a" * 64
    show = {**fixtures["show"], "capabilities": ["completion", "embedding"]}
    return FakeOllama(
        {
            "/api/tags": Scenario.from_json({"json": tags}),
            "/api/show": Scenario.from_json({"json": show}),
            **(overrides or {}),
        }
    )


@asynccontextmanager
async def running_worker(harness, app, **options):
    async with httpx.AsyncClient(
        base_url="http://fake", transport=httpx.ASGITransport(app=app)
    ) as client:
        executor = OllamaExecutor("http://fake", client=client)
        worker = SupabaseWorker(
            harness.workers[0]["rpc"],
            executor,
            heartbeat_seconds=0.1,
            poll_seconds=0.05,
            inventory_seconds=0.2,
            drain_seconds=0,
            systemone=True,
            **options,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        try:
            await asyncio.wait_for(worker.ready.wait(), 5)
            yield worker, task
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)


async def terminal(h, submission, job, task):
    async with asyncio.timeout(5):
        while True:
            if task.done():
                await task
                pytest.fail("Worker stopped unexpectedly")
            page = (await h.read(submission, job))["jobs"][0]
            if page["state"] not in ("queued", "running"):
                return page
            await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    "operation,stream",
    [
        ("generate", False),
        ("chat", False),
        ("embeddings", False),
        ("systemone", False),
        ("generate", True),
        ("chat", True),
    ],
)
def test_worker_sql_lifecycle_exact_response(local_connection, operation, stream):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            app = upstream()
            async with running_worker(h, app) as (_, task):
                payload = {"model": "fixture", "stream": stream}
                if operation == "generate":
                    payload["prompt"] = "hello"
                if operation == "embeddings":
                    payload["input"] = "hello"
                if operation == "systemone":
                    payload.update(state="hello", questions={"refund": "Return score"})
                submission = h.submission(
                    stream=stream, request=normalize_request(operation, payload)
                )
                job = await h.admin.rpc("submit_job", submission)
                page = await terminal(h, submission, job, task)
                assert page["state"] == "succeeded"
                if stream:
                    actual = "".join(
                        chunk["content"] for chunk in page["chunks"]
                    ).encode()
                    assert (
                        actual == (FIXTURES / f"{operation}.legacy.ndjson").read_bytes()
                    )
                    assert json.loads(actual.splitlines()[-1])["done"] is True
                else:
                    expected = json.loads((FIXTURES / "worker.json").read_text())[
                        operation
                    ]
                    assert json.loads(page["result"]["inline"]) == expected

    asyncio.run(run())


def test_inference_silence_does_not_block_control_and_cancel_closes_read(
    local_connection,
):
    async def run():
        gate = asyncio.Event()
        fixtures = json.loads((FIXTURES / "upstream.json").read_text())
        app = upstream(
            {
                "/api/generate": Scenario.from_json(
                    {"json": fixtures["generate"], "first_delay_s": 1}
                )
            }
        )

        async def silence(_):
            await gate.wait()

        app.sleep = silence
        async with queue_harness(local_connection, workers=1) as h:
            async with running_worker(h, app) as (worker, task):
                submission = h.submission(
                    stream=False,
                    request=normalize_request(
                        "generate", {"model": "fixture", "prompt": "hello"}
                    ),
                )
                job = await h.admin.rpc("submit_job", submission)
                async with asyncio.timeout(5):
                    while not worker.jobs:
                        await asyncio.sleep(0.02)
                # Force a short lease while inference is waiting. Heartbeats
                # must renew it, and inventory must still perform local reads.
                scoped_sql(
                    h.cluster,
                    "update oshepherd_spike.jobs set lease_until = clock_timestamp()+interval '0.3 seconds' where id = :'job_id'::uuid and cluster_id = :'cluster_id'::uuid;",
                    job_id=job["job_id"],
                )
                before = len(app.requests)
                await asyncio.sleep(0.6)
                assert len(app.requests) > before
                assert (await h.read(submission, job))["jobs"][0]["state"] == "running"
                await h.admin.rpc(
                    "cancel_job",
                    {
                        **h.caller(),
                        "job_id": job["job_id"],
                        "consumer_token": submission["consumer_token"],
                        "reason": "cancelled",
                    },
                )
                async with asyncio.timeout(5):
                    while worker.jobs:
                        await asyncio.sleep(0.02)
                assert (await h.read(submission, job))["jobs"][0][
                    "state"
                ] == "cancelled"
                assert not task.done()

    asyncio.run(run())


def test_incomplete_stream_fails_after_durable_bytes(local_connection):
    async def run():
        incomplete = '{"model":"fixture","response":"partial","done":false}\n'
        app = upstream({"/api/generate": Scenario.from_json({"body_utf8": incomplete})})
        async with queue_harness(local_connection, workers=1) as h:
            async with running_worker(h, app) as (_, task):
                submission = h.submission(
                    request=normalize_request(
                        "generate",
                        {"model": "fixture", "prompt": "hello", "stream": True},
                    )
                )
                job = await h.admin.rpc("submit_job", submission)
                page = await terminal(h, submission, job, task)
                assert page["state"] == "failed"
                assert page["attempts"] == 1 and page["last_seq"] == 1
                assert page["error"]["code"] == "incomplete_upstream_output"
                assert not json.loads(page["chunks"][0]["content"])["done"]

    asyncio.run(run())
