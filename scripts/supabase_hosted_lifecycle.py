"""Bounded capacity/cancel/deadline/expiry checks for an isolated installation.

Invoked by scripts.supabase_hosted_check --bounded-lifecycle. Maintenance removes
expired data in the supplied test cluster under its existing retention policy.
"""

import asyncio
import json
import time
from uuid import UUID, uuid4

from oshepherd.backends.supabase.client import RpcClient
from oshepherd.backends.supabase.maintenance import ResultMaintenance
from oshepherd.backends.supabase.releases import Database
from tests.support.fake_ollama import Scenario


async def check_lifecycle(client, app, worker, config, state, url, headers, results):
    cluster = str(UUID(str(state.cluster_id)))
    db = Database(config)
    created = []
    completed = set()

    async def submit(prompt, *, limits=None):
        response = await client.post(
            url + "/jobs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "endpoint": "/api/generate",
                "payload": {"model": "fixture", "prompt": prompt},
                "limits": limits or {"queue_ms": 30000, "total_ms": 60000},
            },
        )
        assert response.status_code == 202, "bounded_admission_failed"
        identifier = str(UUID(response.json()["id"]))
        location = url + "/jobs/" + identifier
        created.append((identifier, location))
        assert response.json()["status_url"] == location, "unexpected_async_status_url"
        return identifier, location

    async def status(job):
        response = await client.get(job[1], headers=headers)
        assert response.status_code == 200, "bounded_status_failed"
        data = response.json()
        if data["status"] not in ("queued", "running"):
            completed.add(job[0])
        return data

    async def terminal(job):
        async with asyncio.timeout(45):
            while True:
                data = await status(job)
                if job[0] in completed:
                    return data
                await asyncio.sleep(0.5)

    async def started(prompt):
        async with asyncio.timeout(20):
            while not any(
                item["path"] == "/api/generate" and item["json"].get("prompt") == prompt
                for item in app.requests
            ):
                await asyncio.sleep(0.1)

    def scenario(delay):
        app.overrides["/api/generate"] = Scenario.from_json(
            {
                "json": {
                    "model": "fixture:latest",
                    "response": "bounded fixture",
                    "done": True,
                },
                "first_delay_s": delay,
            }
        )

    try:
        scenario(5)
        burst = await asyncio.gather(
            *(submit("burst-" + str(uuid4())) for _ in range(4))
        )
        peak, queued = 0, False
        async with asyncio.timeout(60):
            while True:
                pages = await asyncio.gather(*(status(job) for job in burst))
                peak = max(peak, len(worker.jobs))
                assert len(worker.jobs) <= 2, "worker_capacity_exceeded"
                queued |= any(page["status"] == "queued" for page in pages)
                if all(job[0] in completed for job in burst):
                    break
                await asyncio.sleep(0.5)
        assert all(page["status"] == "succeeded" for page in pages), "burst_job_failed"
        assert peak == 2 and queued, "capacity_queue_not_observed"
        results.update(burst_succeeded=4, peak_worker_jobs=peak, queued_observed=queued)
        print("hosted_capacity", json.dumps(results), flush=True)

        scenario(30)
        prompt = "explicit-cancel-" + str(uuid4())
        cancelled = await submit(prompt)
        await started(prompt)
        response = await client.delete(cancelled[1], headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        assert (await terminal(cancelled))["status"] == "cancelled"
        async with asyncio.timeout(10):
            while cancelled[0] in worker.jobs:
                await asyncio.sleep(0.1)
        results["explicit_cancel_stops_worker"] = True

        prompt = "silence-deadline-" + str(uuid4())
        silent = await submit(prompt, limits={"queue_ms": 5000, "total_ms": 10000})
        await started(prompt)
        metadata_start = time.monotonic()
        metadata = await client.get(url + "/api/tags", headers=headers)
        assert (
            metadata.status_code == 200 and metadata.json()["models"]
        ), "metadata_stalled"
        results["metadata_during_silence_seconds"] = round(
            time.monotonic() - metadata_start, 2
        )
        session_id = str(UUID(str(worker.session["session_id"])))
        age = await asyncio.to_thread(
            db.execute,
            f"select extract(epoch from clock_timestamp()-heartbeat_at)::text from oshepherd.workers where session_id='{session_id}' and cluster_id='{cluster}';",
        )
        assert float(age) < 10, "heartbeat_stalled_during_silence"
        page = await terminal(silent)
        assert (
            page["status"] in ("failed", "expired")
            and page["error"]["code"] == "execution_expired"
        )
        results["silence_deadline_enforced"] = True
        results["heartbeat_during_silence"] = True
        print("hosted_cancel_deadline", json.dumps(results), flush=True)

        # Shorten only this newly generated successful job's content retention.
        retained = burst[0]
        object_path = await asyncio.to_thread(
            db.execute,
            f"select result->>'object_path' from oshepherd.jobs where id='{retained[0]}' and cluster_id='{cluster}';",
        )
        parts = object_path.split("/")
        assert (
            len(parts) == 4
            and parts[0] == cluster
            and parts[1] == retained[0]
            and parts[3] == "result.json"
        )
        UUID(parts[2])
        await asyncio.to_thread(
            db.execute,
            f"update oshepherd.jobs set content_expires_at=clock_timestamp()+interval '10 seconds' where id='{retained[0]}' and cluster_id='{cluster}' and state='succeeded';",
        )
        response = await client.get(
            retained[1] + "/result?download=true", headers=headers
        )
        assert response.status_code == 200, "short_lived_download_not_signed"
        signed_url = response.json()["url"]
        assert (
            await client.get(signed_url)
        ).status_code == 200, "signed_download_initially_denied"
        await asyncio.sleep(11)
        assert (await client.get(signed_url)).status_code in (
            400,
            401,
            403,
        ), "signed_download_did_not_expire"
        assert (
            await client.get(retained[1] + "/result", headers=headers)
        ).status_code == 410
        page = await status(retained)
        assert page["status"] == "succeeded" and page["result"]["available"] is False
        results["signed_download_expiry"] = True
        results["content_expiry_retains_success_tombstone"] = True

        admin = RpcClient(
            config.supabase_url,
            config.secret_key.get_secret_value(),
            schema="oshepherd_api",
        )
        try:
            maintenance = ResultMaintenance(admin, cluster, batch=16)
            deleted = 0
            # SQL quarantines fresh deletion intents for five minutes. Honor
            # that safety interval rather than accelerating production state.
            maintenance_start = time.monotonic()
            cycles = 0
            async with asyncio.timeout(390):
                while True:
                    counts = await maintenance.cycle()
                    deleted += counts["deleted"]
                    exists = await asyncio.to_thread(
                        db.execute,
                        f"select exists(select 1 from storage.objects where bucket_id='oshepherd-results' and name='{object_path}')::text;",
                    )
                    if exists == "false":
                        break
                    if cycles % 3 == 0:
                        print(
                            "hosted_maintenance_wait_seconds",
                            round(time.monotonic() - maintenance_start),
                            flush=True,
                        )
                    cycles += 1
                    await asyncio.sleep(10)
            results["expired_object_removed"] = True
            results["maintenance_deleted_objects"] = deleted
        finally:
            await admin.close()
        print("hosted_result_lifecycle", json.dumps(results), flush=True)
    finally:
        for identifier, location in created:
            if identifier not in completed:
                try:
                    await client.delete(location, headers=headers, timeout=10)
                except Exception:
                    results["unfinished_job_cancel_ack_lost"] = True
