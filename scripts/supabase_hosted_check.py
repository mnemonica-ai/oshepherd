"""Bounded hosted qualification probe using an explicitly enrolled test worker.

Run with uv run --locked python -m scripts.supabase_hosted_check --help.
Uses real provider HTTP, SQL/Auth/Storage and a loopback fake Ollama socket.
Creates jobs in the supplied installation and temporarily replaces its worker's
model inventory. Use only an isolated test installation with no other workers.
A failed disconnect assertion is a failed check, never a qualification claim.
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


async def run(args):
    config = OperatorConfig.load(args.connection_file)
    if config.project_ref == "local":
        raise ValueError("hosted_connection_required")
    if config.profile == "vercel_hobby" and not config.gateway_url:
        raise ValueError("vercel_gateway_url_required")
    state = Installation.load(args.state_dir, config)
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
            port=55324,
            log_level="critical",
            lifespan="off",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            if server_task.done():
                await server_task
            await asyncio.sleep(0.02)
    executor = OllamaExecutor("http://127.0.0.1:55324")
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
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
