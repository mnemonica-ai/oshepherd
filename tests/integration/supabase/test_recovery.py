"""Real HTTP/SQL recovery and capacity checks using disposable identities."""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from starlette.requests import ClientDisconnect

from oshepherd.api.factory import create_api_app
from oshepherd.backends.supabase.config import SupabaseGatewayConfig

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.sessions import WorkerAuth, WorkerSession
from oshepherd.worker.executor import OllamaExecutor
from tests.support.fake_ollama import Scenario
from .harness import queue_harness, scoped_sql
from .test_fastapi import socket_server
from .test_worker import FIXTURES, running_worker, terminal, upstream


async def wait_until(predicate, seconds=5):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.02)


def silent_upstream(path="/api/generate"):
    gate = asyncio.Event()
    fixtures = json.loads((FIXTURES / "upstream.json").read_text())
    app = upstream(
        {
            path: Scenario.from_json(
                {"json": fixtures[path.rsplit("/", 1)[-1]], "first_delay_s": 1}
            )
        }
    )

    async def silence(_):
        await gate.wait()

    app.sleep = silence
    return app, gate


def test_real_socket_inference_pool_does_not_starve_metadata():
    async def run():
        app, gate = silent_upstream()
        executor = OllamaExecutor("http://127.0.0.1:55325", metadata_seconds=0.5)
        tasks = []
        async with socket_server(app, 55325):
            try:
                tasks = [
                    asyncio.create_task(
                        executor.complete(
                            "generate", {"model": "fixture", "prompt": ""}
                        )
                    )
                    for _ in range(20)
                ]
                await wait_until(
                    lambda: sum(r["path"] == "/api/generate" for r in app.requests)
                    == 20,
                    3,
                )
                assert (await executor.metadata("tags"))["models"]
                assert all(not task.done() for task in tasks)
                gate.set()
                assert all(result["done"] for result in await asyncio.gather(*tasks))
            finally:
                gate.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await executor.close()

    asyncio.run(run())


def test_metadata_total_deadline_closes_stalled_socket_and_recovers():
    async def run():
        app, gate = silent_upstream("/api/tags")
        executor = OllamaExecutor("http://127.0.0.1:55325", metadata_seconds=0.1)
        async with socket_server(app, 55325):
            try:
                with pytest.raises(TimeoutError):
                    await executor.metadata("tags")
                gate.set()
                assert (await executor.metadata("tags"))["models"]
            finally:
                gate.set()
                await executor.close()

    asyncio.run(run())


def test_worker_rotates_real_auth_session_during_inference_silence(
    local_connection, tmp_path
):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            session = next(iter(h.auth_sessions.values()))
            # Keep the real JWT valid; force only the worker's refresh schedule.
            session = session.model_copy(update={"expires_at": int(time.time()) + 60})
            filename = tmp_path / "private" / "session.json"
            session.save(filename)
            auth = WorkerAuth(
                local_connection["API_URL"],
                local_connection["PUBLISHABLE_KEY"],
                client=h.client,
            )
            app, gate = silent_upstream()
            async with running_worker(
                h, app, auth=auth, session=session, session_file=filename
            ) as (worker, task):
                submission = h.submission(
                    stream=False,
                    request={
                        "type": "generate",
                        "payload": {"model": "fixture", "prompt": ""},
                    },
                )
                job = await h.admin.rpc("submit_job", submission)
                await wait_until(lambda: bool(worker.jobs))
                await wait_until(
                    lambda: worker.auth_session.refresh_token != session.refresh_token
                )
                saved = WorkerSession.load(
                    filename, supabase_url=local_connection["API_URL"]
                )
                assert saved.refresh_token == worker.auth_session.refresh_token
                assert (
                    h.workers[0]["rpc"].access_token
                    == saved.access_token.get_secret_value()
                )
                assert (await h.read(submission, job))["jobs"][0]["state"] == "running"
                gate.set()
                assert (await terminal(h, submission, job, task))[
                    "state"
                ] == "succeeded"

    asyncio.run(run())


def test_bounded_parallel_worker_and_saturated_admission(local_connection):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.workers set capacity=8 where cluster_id=:'cluster_id'::uuid; update oshepherd_spike.clusters set max_active=8,max_running=8,max_queued=8,caller_max_active=8 where id=:'cluster_id'::uuid;",
            )
            app, gate = silent_upstream()
            async with running_worker(h, app, concurrency=8) as (worker, task):
                submissions = [
                    h.submission(
                        stream=False,
                        request={
                            "type": "generate",
                            "payload": {"model": "fixture", "prompt": ""},
                        },
                    )
                    for _ in range(8)
                ]
                jobs = await asyncio.gather(
                    *(
                        h.admin.rpc("submit_job", submission)
                        for submission in submissions
                    )
                )
                await wait_until(lambda: len(worker.jobs) == 8)
                with pytest.raises(RpcError) as rejected:
                    await h.admin.rpc(
                        "submit_job", submissions[0] | {"operation_id": str(uuid4())}
                    )
                assert rejected.value.status == 429
                assert not task.done()
                gate.set()
                pages = await asyncio.gather(
                    *(
                        terminal(h, submission, job, task)
                        for submission, job in zip(submissions, jobs)
                    )
                )
                assert all(
                    page["state"] == "succeeded" and page["attempts"] == 1
                    for page in pages
                )
                assert sum(r["path"] == "/api/generate" for r in app.requests) == 8

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,invalid_metadata",
    [
        ("/api/tags", None),
        ("/api/tags", {"models": [{"name": None, "digest": "bad"}]}),
        ("/api/show", {"capabilities": None}),
        ("/api/show", {"capabilities": "completion"}),
    ],
)
def test_invalid_metadata_preserves_inventory_and_control_during_inference(
    local_connection, path, invalid_metadata
):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            app, gate = silent_upstream()
            async with running_worker(h, app) as (worker, task):
                submission = h.submission(
                    stream=False,
                    request={
                        "type": "generate",
                        "payload": {"model": "fixture", "prompt": ""},
                    },
                )
                job = await h.admin.rpc("submit_job", submission)
                await wait_until(lambda: bool(worker.jobs))
                original = app.overrides.get(path)
                app.overrides[path] = Scenario.from_json({"json": invalid_metadata})
                before = len(app.requests)
                await asyncio.sleep(0.5)
                assert len(app.requests) > before and not task.done()
                assert (await h.read(submission, job))["jobs"][0]["state"] == "running"
                if original is None:
                    app.overrides.pop(path)
                else:
                    app.overrides[path] = original
                gate.set()
                assert (await terminal(h, submission, job, task))[
                    "state"
                ] == "succeeded"

    asyncio.run(run())


@pytest.mark.parametrize("durable_output", [False, True])
def test_worker_restart_fences_attempt_and_retries_only_before_output(
    local_connection, durable_output
):
    asyncio.run(_restart(local_connection, durable_output))


async def _restart(connection, durable_output):
    async with queue_harness(connection, workers=1) as h:
        lines = (
            (FIXTURES / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)
        )
        gate = asyncio.Event()
        app = upstream(
            {
                "/api/generate": Scenario(
                    tuple(lines),
                    content_type="application/x-ndjson",
                    first_delay_s=0 if durable_output else 1,
                    gaps_s=(1, 1),
                )
            }
        )

        async def silence(_):
            await gate.wait()

        app.sleep = silence
        submission = h.submission(
            request={
                "type": "generate",
                "payload": {"model": "fixture", "prompt": "", "stream": True},
            }
        )
        async with socket_server(app, 55325):
            try:
                async with running_worker(
                    h, app, upstream_url="http://127.0.0.1:55325"
                ) as (worker, task):
                    job = await h.admin.rpc("submit_job", submission)
                    await wait_until(lambda: bool(worker.jobs))
                    generation = worker.session["generation"]
                    if durable_output:
                        async with asyncio.timeout(5):
                            while (await h.read(submission, job))["jobs"][0][
                                "last_seq"
                            ] == 0:
                                await asyncio.sleep(0.02)
                    task.cancel()
                    await task
            except asyncio.CancelledError:
                pass
            finally:
                gate.set()
        async with running_worker(h, upstream()) as (worker, task):
            assert worker.session["generation"] > generation
            page = await terminal(h, submission, job, task)
            if durable_output:
                assert page["state"] == "failed" and page["attempts"] == 1
                assert page["last_seq"] > 0
            else:
                assert page["state"] == "succeeded" and page["attempts"] == 2


def test_blocked_asgi_reader_cancels_real_sql_job_and_worker(local_connection):
    async def run():
        lines = (
            (FIXTURES / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)
        )
        gate = asyncio.Event()
        app = upstream(
            {
                "/api/generate": Scenario(
                    tuple(lines), content_type="application/x-ndjson", gaps_s=(1, 1)
                )
            }
        )

        async def silence(_):
            await gate.wait()

        app.sleep = silence
        async with queue_harness(local_connection, workers=1) as h:
            api = create_api_app(
                SupabaseGatewayConfig(
                    SUPABASE_URL=local_connection["API_URL"],
                    SUPABASE_SECRET_KEY=local_connection["SECRET_KEY"],
                    SUPABASE_SCHEMA="oshepherd_spike_api",
                    OSHEPHERD_CLUSTER_ID=h.cluster,
                    OSHEPHERD_READER_STALL_SECONDS=0.1,
                ),
                backend="supabase",
            )
            body = json.dumps(
                {"model": "fixture", "prompt": "", "stream": True}
            ).encode()
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": body}
                await asyncio.Event().wait()

            async def send(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    await asyncio.Event().wait()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/generate",
                "raw_path": b"/api/generate",
                "query_string": b"",
                "headers": [(b"authorization", ("Bearer " + h.keys[0]).encode())],
                "server": ("gateway", 80),
                "client": ("client", 1),
            }
            async with socket_server(app, 55325):
                try:
                    async with running_worker(
                        h, app, upstream_url="http://127.0.0.1:55325"
                    ) as (worker, task):
                        with pytest.raises(ClientDisconnect):
                            await asyncio.wait_for(api(scope, receive, send), 5)
                        output = scoped_sql(
                            h.cluster,
                            "select state,error->>'code' from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid;",
                        )
                        assert "cancelled|reader_stalled" in "".join(output.split())
                        await wait_until(lambda: not worker.jobs)
                        assert not task.done()
                finally:
                    gate.set()

    asyncio.run(run())
