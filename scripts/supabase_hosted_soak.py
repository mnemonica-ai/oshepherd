"""Finite, real Auth-expiry and deterministic load measurement on owned targets.

Invoked through supabase_hosted_check --soak, never by shortening JWT lifetimes.
No prompts, output, tokens or provider credentials are written to the report.
"""

import asyncio
import json
from pathlib import Path
import time

from oshepherd.backends.supabase.private_files import write_private_json
from oshepherd.backends.supabase.sessions import WorkerSession


async def check_soak(
    client,
    worker,
    task,
    config,
    state,
    url,
    headers,
    session_file,
    report_file,
    results,
    *,
    load_jobs=1000,
):
    original = worker.auth_session
    assert load_jobs in (0, 1000), "invalid_soak_load_budget"
    remaining = original.expires_at - time.time()
    assert 60 < remaining <= 3605, "unexpected_worker_auth_lifetime"
    fixture = next(
        case
        for case in json.loads(
            (
                Path(__file__).resolve().parents[1] / "protocol/v1/fixtures/routes.json"
            ).read_text()
        )["cases"]
        if case["id"] == "generate-defaults"
    )
    started = time.monotonic()
    latencies = []
    calls = {}
    rpc = worker.rpc.rpc

    async def counted(name, *args, **kwargs):
        calls[name] = calls.get(name, 0) + 1
        return await rpc(name, *args, **kwargs)

    worker.rpc.rpc = counted
    results.update(
        soak_complete=False,
        deterministic_jobs=0,
        concurrency=2,
        original_auth_remaining_seconds=round(remaining),
    )

    def checkpoint():
        results.update(
            elapsed_seconds=round(time.monotonic() - started, 2),
            worker_rpc_calls=dict(calls),
        )
        if report_file:
            write_private_json(report_file, results)

    def alive():
        assert not task.done(), "soak_worker_stopped"

    async def request():
        alive()
        before = time.monotonic()
        response = await client.post(
            url + fixture["request"]["path"],
            headers=headers,
            json=fixture["request"]["json"],
            timeout=145,
        )
        assert response.status_code == 200, "soak_request_failed"
        assert response.json() == fixture["response"]["json"], "soak_fixture_mismatch"
        latencies.append(time.monotonic() - before)
        results["deterministic_jobs"] += 1

    async def monitor():
        while True:
            alive()
            metadata = await client.get(url + "/api/tags", headers=headers)
            assert metadata.status_code == 200, "soak_metadata_unavailable"
            results["metadata_checks"] = results.get("metadata_checks", 0) + 1
            results["auth_refreshed"] = (
                worker.auth_session.expires_at > original.expires_at
                and worker.auth_session.access_token != original.access_token
            )
            checkpoint()
            print(
                "hosted_soak_progress",
                json.dumps(
                    {
                        key: results[key]
                        for key in (
                            "elapsed_seconds",
                            "deterministic_jobs",
                            "auth_refreshed",
                        )
                    }
                ),
                flush=True,
            )
            await asyncio.sleep(30)

    monitor_task = asyncio.create_task(monitor())
    try:
        async with asyncio.timeout(4500):
            results["load_jobs_target"] = load_jobs
            for _ in range(load_jobs // 2):
                alive()
                if monitor_task.done():
                    await monitor_task
                await asyncio.gather(request(), request())
            results["load_seconds"] = round(time.monotonic() - started, 2)
            if latencies:
                ordered = sorted(latencies)
                results.update(
                    latency_p50_seconds=round(ordered[499], 3),
                    latency_p95_seconds=round(ordered[949], 3),
                    latency_max_seconds=round(ordered[-1], 3),
                )
            # Reduce idle polling while keeping independent heartbeat/control alive.
            worker.interval = 5
            while time.time() <= original.expires_at + 10:
                alive()
                if monitor_task.done():
                    await monitor_task
                await asyncio.sleep(5)
            assert results.get("auth_refreshed"), "worker_auth_not_refreshed"
            saved = WorkerSession.load(session_file, supabase_url=config.supabase_url)
            assert saved == worker.auth_session, "refreshed_session_not_persisted"
            auth_headers = {"apikey": config.publishable_key.get_secret_value()}
            old = await client.get(
                config.supabase_url + "/auth/v1/user",
                headers={
                    **auth_headers,
                    "Authorization": "Bearer "
                    + original.access_token.get_secret_value(),
                },
            )
            current = await client.get(
                config.supabase_url + "/auth/v1/user",
                headers={
                    **auth_headers,
                    "Authorization": "Bearer " + saved.access_token.get_secret_value(),
                },
            )
            results["original_auth_status"] = old.status_code
            # GoTrue uses 403 for invalid JWTs; gateways may use 401. A 200
            # remains a failed expiry check, never a qualification claim.
            assert old.status_code in (401, 403), "original_auth_not_expired"
            assert current.status_code == 200, "refreshed_auth_not_accepted"
            assert current.json()["id"] == str(
                original.user_id
            ), "auth_identity_changed"
            await request()
            results.update(
                soak_complete=True,
                original_auth_rejected=True,
                refreshed_auth_accepted=True,
                refreshed_session_persisted=True,
                post_expiry_inference=True,
                hosted_qualified=False,
            )
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        worker.rpc.rpc = rpc
        checkpoint()
