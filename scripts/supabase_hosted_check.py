"""Bounded hosted qualification probe using an explicitly enrolled test worker.

Run with uv run --locked python -m scripts.supabase_hosted_check --help.
Uses real provider HTTP, SQL/Auth/Storage and a loopback fake Ollama socket.
Creates jobs in the supplied installation and temporarily replaces its worker's
model inventory. Use only an isolated test installation with no other workers.
A failed disconnect assertion is a failed check, never a qualification claim.
Use --lifetimes for a separate bounded synchronous/async duration check.
Use --bounded-lifecycle for capacity, explicit cancellation, deadline, expiry
and maintenance checks; cleanup honors the five-minute deletion quarantine.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import httpx
import uvicorn

from oshepherd.backends.supabase.client import RpcClient
from oshepherd.backends.supabase.operator import OperatorConfig, Installation
from oshepherd.backends.supabase.private_files import write_private_json
from oshepherd.backends.supabase.releases import Database
from oshepherd.backends.supabase.sessions import WorkerAuth, WorkerSession
from oshepherd.backends.supabase.storage import WorkerResultStorage
from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.worker.executor import OllamaExecutor
from tests.integration.supabase.test_worker import upstream
from tests.support.fake_ollama import Scenario


async def check_lifetimes(client, app, url, headers, results):
    """Real hosted deadlines; async work outlives a gateway invocation."""
    completion = {
        "model": "fixture:latest",
        "response": "lifetime fixture",
        "done": True,
    }
    marker = "lifetime-async-" + str(uuid4())
    app.overrides["/api/generate"] = Scenario.from_json(
        {"json": completion, "first_delay_s": 405}
    )
    accepted = await client.post(
        url + "/jobs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "endpoint": "/api/generate",
            "payload": {"model": "fixture", "prompt": marker},
        },
    )
    assert accepted.status_code == 202, "lifetime_async_admission_failed"
    job_id = str(UUID(accepted.json()["id"]))
    job_url = url.rstrip("/") + "/jobs/" + job_id
    sync_task = None
    succeeded = False
    try:
        assert accepted.json()["status_url"] == job_url, "unexpected_async_status_url"
        async with asyncio.timeout(30):
            while not any(
                item["path"] == "/api/generate" and item["json"].get("prompt") == marker
                for item in app.requests
            ):
                await asyncio.sleep(0.1)
        started = time.monotonic()
        results["async_upstream_delay_seconds"] = 405
        # The first scenario has already been captured by the upstream socket.
        app.overrides["/api/generate"] = Scenario.from_json(
            {"json": completion, "first_delay_s": 145}
        )

        async def synchronous():
            initial = time.monotonic()
            response = await client.post(
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": "lifetime-sync", "stream": False},
                timeout=160,
            )
            error = response.json().get("error")
            results.update(
                sync_status=response.status_code,
                sync_seconds=round(time.monotonic() - initial, 2),
                sync_expired=error == "execution_expired",
            )
            assert (
                response.status_code == 504 and results["sync_expired"]
            ), "sync_deadline_not_enforced"
            print("hosted_sync_lifetime", json.dumps(results), flush=True)

        sync_task = asyncio.create_task(synchronous())
        async with asyncio.timeout(450):
            polls = 0
            while True:
                if sync_task.done():
                    await sync_task
                response = await client.get(job_url, headers=headers)
                assert response.status_code == 200, "lifetime_status_failed"
                state = response.json()["status"]
                if state not in ("queued", "running"):
                    results.update(
                        async_status=state,
                        async_seconds=round(time.monotonic() - started, 2),
                    )
                    assert state == "succeeded", "async_lifetime_failed"
                    break
                if polls % 3 == 0:
                    print(
                        "hosted_lifetime_progress_seconds",
                        round(time.monotonic() - started),
                        flush=True,
                    )
                polls += 1
                await asyncio.sleep(10)
        result = await client.get(job_url + "/result", headers=headers)
        results["async_done"] = (
            result.status_code == 200 and result.json().get("done") is True
        )
        assert (
            results["async_done"] and results["async_seconds"] >= 400
        ), "async_lifetime_not_verified"
        await sync_task
        succeeded = True
        print("hosted_lifetimes", json.dumps(results), flush=True)
    finally:
        if not succeeded:
            try:
                cancelled = await client.delete(job_url, headers=headers, timeout=10)
                results["async_cancel_acknowledged"] = cancelled.status_code == 200
            except httpx.HTTPError:
                results["async_cancel_acknowledged"] = False
        if sync_task is not None:
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)


async def run(args):
    config = OperatorConfig.load(args.connection_file)
    if config.project_ref == "local":
        raise ValueError("hosted_connection_required")
    if config.profile == "vercel_hobby" and not config.gateway_url:
        raise ValueError("vercel_gateway_url_required")
    state = Installation.load(args.state_dir, config)
    if state.stage != "configured":
        raise ValueError("qualification_requires_configured_installation")
    if any(
        getattr(args, name, False)
        for name in ("bounded_lifecycle", "soak", "auth_soak", "recovery", "envelope")
    ):
        cluster = str(UUID(str(state.cluster_id)))
        active = await asyncio.to_thread(
            Database(config).execute,
            f"select count(*) from oshepherd.jobs where cluster_id='{cluster}' and state in ('queued','running');",
        )
        if active != "0":
            raise ValueError("bounded_lifecycle_requires_idle_test_installation")
    saved = WorkerSession.load(args.worker_session, supabase_url=config.supabase_url)
    if saved.user_id not in {
        item.auth_subject
        for item in state.workers.values()
        if not item.revocation_requested
    }:
        raise ValueError("worker_not_owned_by_test_installation")
    if not any(
        item.auth_subject == saved.user_id and item.capacity >= 2
        for item in state.workers.values()
    ):
        raise ValueError("qualification_worker_requires_capacity_two")
    auth = WorkerAuth(config.supabase_url, config.publishable_key.get_secret_value())
    if saved.expires_at <= time.time() + 60:
        try:
            saved = await auth.refresh(saved)
            saved.save(args.worker_session)
        except BaseException:
            await auth.close()
            raise
    rpc = RpcClient(
        config.supabase_url,
        config.publishable_key.get_secret_value(),
        access_token=saved.access_token.get_secret_value(),
    )
    fixtures = Path(__file__).resolve().parents[1] / "protocol/v1/fixtures"
    app = upstream()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=getattr(args, "upstream_port", 55324),
            log_level="critical",
            lifespan="off",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
    )
    server_task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.02)
    executor = OllamaExecutor(
        "http://127.0.0.1:" + str(getattr(args, "upstream_port", 55324))
    )
    worker = SupabaseWorker(
        rpc,
        executor,
        concurrency=2,
        heartbeat_seconds=1,
        poll_seconds=0.5,
        drain_seconds=0,
        systemone=True,
        auth=auth,
        session=saved,
        session_file=args.worker_session,
        result_storage=WorkerResultStorage(rpc),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    results = {}
    url = config.gateway_url or config.supabase_url + "/functions/v1/ollama"
    headers = {"Authorization": "Bearer " + state.application_key.get_secret_value()}
    try:
        await asyncio.wait_for(worker.ready.wait(), 30)
        async with httpx.AsyncClient(timeout=30) as client:
            assert (await client.get(url + "/health")).status_code == 401
            assert (
                await client.get(url + "/health", headers=headers)
            ).status_code == 200
            results["auth"] = True
            if getattr(args, "lifetimes", False):
                await check_lifetimes(client, app, url, headers, results)
                return
            if getattr(args, "bounded_lifecycle", False):
                from scripts.supabase_hosted_lifecycle import check_lifecycle

                await check_lifecycle(
                    client, app, worker, config, state, url, headers, results
                )
                return
            if getattr(args, "soak", False) or getattr(args, "auth_soak", False):
                from scripts.supabase_hosted_soak import check_soak

                await check_soak(
                    client,
                    worker,
                    task,
                    config,
                    state,
                    url,
                    headers,
                    args.worker_session,
                    args.report_file,
                    results,
                    load_jobs=0 if getattr(args, "auth_soak", False) else 1000,
                )
                return
            if getattr(args, "recovery", False):
                from scripts.supabase_hosted_recovery import check_recovery

                await check_recovery(
                    client, app, worker, task, config, state, url, headers, results
                )
                return
            if getattr(args, "envelope", False):
                from scripts.supabase_hosted_envelope import check_envelope

                await check_envelope(
                    client, app, worker, config, state, url, headers, results
                )
                return
            cases = json.loads((fixtures / "routes.json").read_text())["cases"]
            for name in (
                "generate-defaults",
                "chat-defaults",
                "embeddings-defaults",
                "systemone-text",
                "systemone-extras-always-nonstream",
                "generate-stream",
                "chat-stream",
            ):
                fixture = next(item for item in cases if item["id"] == name)
                response = await client.post(
                    url + fixture["request"]["path"],
                    headers=headers,
                    json=fixture["request"]["json"],
                )
                assert response.status_code == 200, (
                    name + "_status_" + str(response.status_code)
                )
                expected = fixture["response"]
                if "body_file" in expected:
                    assert (
                        response.content
                        == (fixtures / expected["body_file"]).read_bytes()
                    ), (name + "_bytes")
                elif "body" in expected:
                    assert response.text == expected["body"], name + "_body"
                else:
                    assert response.json() == expected["json"], name + "_json"
                results[name] = True
            for name in ("tags", "ps", "version"):
                assert (
                    await client.get(url + "/api/" + name, headers=headers)
                ).status_code == 200
                results[name] = True
            assert (
                await client.post(
                    url + "/api/show",
                    headers=headers,
                    json={"model": "fixture", "verbose": True},
                )
            ).status_code == 200
            results["show"] = True
            output_size = 4200000 if config.profile == "vercel_hobby" else 1200000
            large = {
                "model": "fixture:latest",
                "response": "x" * output_size,
                "done": True,
            }
            app.overrides["/api/generate"] = Scenario.from_json({"json": large})
            idem = str(uuid4())
            payload = {
                "endpoint": "/api/generate",
                "payload": {"model": "fixture", "prompt": "large"},
            }
            accepted = await client.post(
                url + "/jobs",
                headers={**headers, "Idempotency-Key": idem},
                json=payload,
            )
            assert accepted.status_code == 202, "async_submit_" + str(
                accepted.status_code
            )
            job_url = accepted.json()["status_url"]
            replay = await client.post(
                url + "/jobs",
                headers={**headers, "Idempotency-Key": idem},
                json=payload,
            )
            assert replay.json()["id"] == accepted.json()["id"]
            async with asyncio.timeout(60):
                while True:
                    status = (await client.get(job_url, headers=headers)).json()
                    if status["status"] not in ("queued", "running"):
                        break
                    await asyncio.sleep(0.5)
            assert status["status"] == "succeeded", "async_terminal_" + status["status"]
            assert (
                await client.get(job_url + "/result", headers=headers)
            ).status_code == 413
            response = await client.get(
                job_url + "/result?download=true", headers=headers
            )
            assert response.status_code == 200, "download_" + str(response.status_code)
            desc = response.json()
            blob = await client.get(desc["url"])
            assert blob.status_code == 200 and len(blob.content) == desc["size_bytes"]
            assert hashlib.sha256(blob.content).hexdigest() == desc["sha256"]
            results["async_replay_private_download"] = True
            results["download_bytes"] = len(blob.content)
            print("hosted_routes", json.dumps(results), flush=True)
            lines = (
                (fixtures / "generate.upstream.ndjson")
                .read_bytes()
                .splitlines(keepends=True)
            )
            app.overrides["/api/generate"] = Scenario(
                tuple(lines), content_type="application/x-ndjson", gaps_s=(4, 4)
            )
            started = time.monotonic()
            async with client.stream(
                "POST",
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": "disconnect", "stream": True},
            ) as response:
                assert response.status_code == 200
                first = await anext(response.aiter_lines())
                assert json.loads(first)["done"] is False
                stream_jobs = [
                    identifier
                    for identifier, (claim, _) in worker.jobs.items()
                    if claim["request"]["payload"].get("stream") is True
                ]
                assert len(stream_jobs) == 1, "streaming_job_not_active_before_terminal"
                job = UUID(stream_jobs[0])
                results["first_record_seconds"] = round(time.monotonic() - started, 2)
            async with asyncio.timeout(20):
                while worker.jobs:
                    await asyncio.sleep(0.2)
            db = Database(config)
            terminal = await asyncio.to_thread(
                db.execute,
                f"select state from oshepherd.jobs where id='{job}' and cluster_id='{state.cluster_id}';",
            )
            results["disconnect_state"] = terminal
            results["disconnect_cancelled"] = terminal == "cancelled"
            print(
                "hosted_disconnect",
                json.dumps(
                    {
                        k: v
                        for k, v in results.items()
                        if k.startswith(("first_record", "disconnect"))
                    }
                ),
                flush=True,
            )
    finally:
        stop.set()
        try:
            if not task.cancelled():
                await asyncio.wait_for(task, 15)
        finally:
            await rpc.close()
            await auth.close()
            await executor.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, 5)
            if args.report_file:
                write_private_json(args.report_file, results)

    assert results.get(
        "disconnect_cancelled"
    ), "reader_disconnect_did_not_cancel_sql_job"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--worker-session", type=Path, required=True)
    parser.add_argument("--report-file", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--envelope",
        action="store_true",
        help="Bounded request/JSON/NDJSON size checks and slow-reader/control observations on an idle isolated installation (at most 10 minutes).",
    )
    modes.add_argument(
        "--lifetimes",
        action="store_true",
        help="Measure the 135-second sync deadline and a 405-second async upstream; separate from strict cancellation.",
    )
    modes.add_argument(
        "--recovery",
        action="store_true",
        help="Measure worker restarts/fencing, retry exclusion after durable output, incomplete EOF and a bounded slow HTTP reader on an idle installation.",
    )
    modes.add_argument(
        "--auth-soak",
        action="store_true",
        help="Measure a real Auth expiry/refresh cycle without repeating the already measured load; reports load_jobs_target=0.",
    )
    modes.add_argument(
        "--soak",
        action="store_true",
        help="Run 1,000 exact deterministic jobs and remain online through actual worker Auth expiry/refresh (at most 75 minutes). Isolated idle installations only.",
    )
    modes.add_argument(
        "--bounded-lifecycle",
        action="store_true",
        help="Test four-job capacity, explicit cancellation, silence deadlines, signed/content expiry and cluster-scoped maintenance in an isolated installation.",
    )
    parser.add_argument(
        "--upstream-port",
        type=int,
        default=55324,
        help="Loopback fake-upstream port (default: 55324).",
    )
    args = parser.parse_args()
    if not 1024 <= args.upstream_port <= 65535:
        parser.error("--upstream-port must be between 1024 and 65535")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
