"""Finite hosted restart/fencing, incomplete EOF and slow-reader measurements.

Invoked by supabase_hosted_check --recovery on an idle owned installation.
Never infer reader-disconnect propagation from these independent workloads.
"""

import asyncio
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
from uuid import UUID, uuid4

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.releases import Database
from oshepherd.backends.supabase.worker import SupabaseWorker
from tests.support.fake_ollama import Scenario


async def check_recovery(
    client, app, worker, task, config, state, url, headers, results
):
    db = Database(config)
    cluster = str(UUID(str(state.cluster_id)))
    active_worker, active_task = worker, task
    stop = asyncio.Event()
    owned = []
    fixtures = Path(__file__).resolve().parents[1] / "protocol/v1/fixtures"
    upstream = (
        (fixtures / "generate.upstream.ndjson").read_bytes().splitlines(keepends=True)
    )

    async def sql(identifier):
        identifier = str(UUID(identifier))
        text = await asyncio.to_thread(
            db.execute,
            "select json_build_object('state',state,'attempts',attempts,'last_seq',last_seq,"
            "'output_bytes',output_bytes,'error',error)->> 'state' from oshepherd.jobs "
            f"where id='{identifier}' and cluster_id='{cluster}';",
        )
        return text

    async def summary(identifier):
        identifier = str(UUID(identifier))
        text = await asyncio.to_thread(
            db.execute,
            "select json_build_object('state',state,'attempts',attempts,'last_seq',last_seq,"
            "'output_bytes',output_bytes,'error',error) from oshepherd.jobs "
            f"where id='{identifier}' and cluster_id='{cluster}';",
        )
        return json.loads(text)

    async def identify(marker):
        assert marker.startswith("recovery-")
        UUID(marker.removeprefix("recovery-"))
        async with asyncio.timeout(20):
            while True:
                identifier = await asyncio.to_thread(
                    db.execute,
                    f"select id::text from oshepherd.jobs where cluster_id='{cluster}' "
                    f"and request #>> '{{payload,prompt}}'='{marker}';",
                )
                if identifier:
                    identifier = str(UUID(identifier))
                    owned.append(identifier)
                    return identifier
                await asyncio.sleep(0.2)

    async def terminal(identifier):
        async with asyncio.timeout(40):
            while True:
                assert not active_task.done(), "recovery_worker_stopped"
                data = await summary(identifier)
                if data["state"] not in ("queued", "running"):
                    return data
                await asyncio.sleep(0.5)

    async def restart(identifier):
        nonlocal active_worker, active_task, stop
        async with asyncio.timeout(15):
            while identifier not in active_worker.jobs:
                await asyncio.sleep(0.1)
        claim = active_worker.jobs[identifier][0].copy()
        previous = dict(active_worker.session)
        saved_auth = active_worker.auth_session
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)
        app.overrides.pop("/api/generate", None)
        active_worker = SupabaseWorker(
            worker.rpc,
            worker.executor,
            concurrency=2,
            heartbeat_seconds=1,
            poll_seconds=0.5,
            drain_seconds=0,
            systemone=True,
            auth=worker.auth,
            session=saved_auth,
            session_file=worker.session_file,
            result_storage=worker.result_storage,
        )
        stop = asyncio.Event()
        active_task = asyncio.create_task(active_worker.run(stop))
        await asyncio.wait_for(active_worker.ready.wait(), 30)
        assert (
            active_worker.session["generation"] > previous["generation"]
        ), "generation_not_advanced"
        assert (
            active_worker.session["session_id"] != previous["session_id"]
        ), "session_not_rotated"
        fields = {**previous, "job_id": identifier, "attempt_id": claim["attempt_id"]}
        for name, payload in (
            (
                "heartbeat_and_claim",
                {**previous, "active_attempts": [], "max_jobs": 0, "draining": False},
            ),
            (
                "append_output",
                {
                    **fields,
                    "seq": 1,
                    "content": "{}\n",
                    "checksum": hashlib.sha256(b"{}\n").hexdigest(),
                },
            ),
            (
                "fail_attempt",
                {
                    **fields,
                    "retryable": True,
                    "error": {"code": "upstream_unavailable", "message": "fixture"},
                },
            ),
        ):
            try:
                await worker.rpc.rpc(name, payload, attempts=1)
            except RpcError as error:
                assert error.code in (
                    "stale_session",
                    "stale_attempt",
                ), "unexpected_fencing_error"
            else:
                raise AssertionError("old_generation_mutation_accepted")
        results["restarts"] = results.get("restarts", 0) + 1

    try:
        async with asyncio.timeout(240):
            marker = "recovery-" + str(uuid4())
            app.overrides["/api/generate"] = Scenario.from_json(
                {
                    "json": app.responses["generate"],
                    "first_delay_s": 30,
                }
            )
            accepted = await client.post(
                url + "/jobs",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "endpoint": "/api/generate",
                    "payload": {"model": "fixture", "prompt": marker},
                    "limits": {"queue_ms": 30000, "total_ms": 60000},
                },
            )
            assert accepted.status_code == 202, "recovery_async_admission_failed"
            identifier = str(UUID(accepted.json()["id"]))
            owned.append(identifier)
            assert (
                accepted.json()["status_url"] == url + "/jobs/" + identifier
            ), "unexpected_async_status_url"
            await restart(identifier)
            data = await terminal(identifier)
            assert (
                data["state"] == "succeeded" and data["attempts"] == 2
            ), "pre_output_retry_not_verified"
            results["restart_before_output_retries_once"] = True

            marker = "recovery-" + str(uuid4())
            app.overrides["/api/generate"] = Scenario(
                tuple(upstream), content_type="application/x-ndjson", gaps_s=(30, 30)
            )
            async with client.stream(
                "POST",
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": marker, "stream": True},
                timeout=60,
            ) as response:
                assert response.status_code == 200
                reader = response.aiter_lines()
                assert json.loads(await anext(reader))["done"] is False
                identifier = await identify(marker)
                assert (await summary(identifier))[
                    "last_seq"
                ] > 0, "partial_output_not_durable"
                await restart(identifier)
                async for _ in reader:
                    pass
            data = await terminal(identifier)
            assert (
                data["state"] == "failed"
                and data["attempts"] == 1
                and data["last_seq"] > 0
            ), "durable_output_retried"
            assert data["error"]["code"] == "worker_lost"
            results["restart_after_output_never_retries"] = True

            marker = "recovery-" + str(uuid4())
            app.overrides["/api/generate"] = Scenario(
                (upstream[0],), content_type="application/x-ndjson", first_delay_s=1
            )
            response = await client.post(
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": marker, "stream": True},
                timeout=60,
            )
            assert response.status_code == 200, "incomplete_stream_not_started"
            identifier = await identify(marker)
            data = await terminal(identifier)
            assert (
                data["state"] == "failed"
                and data["attempts"] == 1
                and data["last_seq"] > 0
            ), "incomplete_output_retried"
            assert data["error"]["code"] == "incomplete_upstream_output"
            results["incomplete_eof_after_output_never_retries"] = True

            # A bounded actual slow HTTP reader; this does not prove where a
            # hosting proxy buffers or establish a maximum backpressure envelope.
            raw = json.loads(upstream[0])
            raw["response"] = "x" * 16384
            piece = json.dumps(raw, ensure_ascii=False).encode() + b"\n"
            final = upstream[-1]
            app.overrides["/api/generate"] = Scenario(
                tuple([piece] * 32 + [final]), content_type="application/x-ndjson"
            )
            marker = "recovery-" + str(uuid4())
            before = time.monotonic()
            content = bytearray()
            async with client.stream(
                "POST",
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": marker, "stream": True},
                timeout=60,
            ) as response:
                assert response.status_code == 200
                async for fragment in response.aiter_bytes(chunk_size=4096):
                    content.extend(fragment)
                    await asyncio.sleep(0.05)
            expected_piece = json.loads(
                (fixtures / "generate.legacy.ndjson").read_bytes().splitlines()[0]
            )
            expected_piece["response"] = raw["response"]
            expected = (json.dumps(expected_piece).encode() + b"\n") * 32
            expected += (
                (fixtures / "generate.legacy.ndjson")
                .read_bytes()
                .splitlines(keepends=True)[-1]
            )
            assert bytes(content) == expected, "slow_reader_ndjson_mismatch"
            identifier = await identify(marker)
            assert (await terminal(identifier))["state"] == "succeeded"
            results.update(
                slow_reader_exact_bytes=len(content),
                slow_reader_seconds=round(time.monotonic() - before, 2),
                old_generation_mutations_fenced=True,
                hosted_qualified=False,
            )
            maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            results["probe_process_max_rss_bytes"] = (
                maximum if sys.platform == "darwin" else maximum * 1024
            )
            print("hosted_recovery", json.dumps(results), flush=True)
    finally:
        for identifier in owned:
            try:
                if await sql(identifier) in ("queued", "running"):
                    await client.delete(
                        url + "/jobs/" + identifier, headers=headers, timeout=10
                    )
            except Exception:
                results["cleanup_incomplete"] = True
        if active_task is not task:
            stop.set()
            await asyncio.wait_for(active_task, 15)
