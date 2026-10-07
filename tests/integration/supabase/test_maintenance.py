"""Real durable cleanup, lost acknowledgements, fencing and orphan isolation."""

import asyncio
import json
import uuid

import httpx
import pytest

from oshepherd.backends.supabase.client import RpcClient, RpcError
from oshepherd.backends.supabase.maintenance import ResultMaintenance
from oshepherd.backends.supabase.storage import WorkerResultStorage
from scripts import supabase_local
from .harness import queue_harness, scoped_sql
from .test_queue import checksum


async def uploaded(h, worker):
    submission = h.submission(stream=False, mode="async")
    job = await h.admin.rpc("submit_job", submission)
    claim = (await h.claim(worker))["jobs"][0]
    descriptor = await WorkerResultStorage(worker["rpc"]).upload(
        h.cluster, claim, b'{"done":true}'
    )
    return submission, job, claim, descriptor


def make_deletion_due(h):
    scoped_sql(
        h.cluster,
        "update oshepherd_spike.result_deletions set available_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
    )


def test_cleanup_intent_survives_purge_and_claim_ack_loss_and_checks_storage(
    local_connection,
):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            submission, job, claim, descriptor = await uploaded(h, worker)
            await worker["rpc"].rpc(
                "finish_job",
                {**h.attempt(worker, claim), "final_chunks": [], "result": descriptor},
            )
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set content_expires_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            # Ignore the first purge acknowledgement. Its second response has
            # no paths, but durable intent must still recover the actual object.
            await h.admin.rpc("purge_expired", {"cluster_id": h.cluster, "limit": 1})
            replayed_purge = await h.admin.rpc(
                "purge_expired", {"cluster_id": h.cluster, "limit": 1}
            )
            assert replayed_purge["object_paths"] == []
            make_deletion_due(h)
            request = {
                "cluster_id": h.cluster,
                "limit": 1,
                "operation_id": str(uuid.uuid4()),
            }
            tick = await h.admin.rpc("maintenance_tick", request)
            assert tick["object_paths"] == [descriptor["object_path"]]
            assert tick == await h.admin.rpc("maintenance_tick", request)
            assert (
                await h.admin.rpc(
                    "maintenance_tick", {"cluster_id": h.cluster, "limit": 1}
                )
            )["object_paths"] == []
            # A provider success response without actual removal cannot erase
            # the deletion intent. SQL verifies Storage metadata independently.
            falsely_reported = await h.admin.rpc(
                "complete_maintenance",
                {
                    "cluster_id": h.cluster,
                    "claim_id": tick["claim_id"],
                    "deleted_paths": tick["object_paths"],
                    "failed_paths": [],
                },
            )
            assert falsely_reported == {"deleted": 0, "retrying": 1}
            make_deletion_due(h)
            newer = await h.admin.rpc(
                "maintenance_tick", {"cluster_id": h.cluster, "limit": 1}
            )
            assert newer["claim_id"] != tick["claim_id"]
            assert newer["object_paths"] == tick["object_paths"]
            with pytest.raises(RpcError, match="stale_cleanup"):
                await h.admin.rpc(
                    "complete_maintenance",
                    {
                        "cluster_id": h.cluster,
                        "claim_id": tick["claim_id"],
                        "deleted_paths": tick["object_paths"],
                        "failed_paths": [],
                    },
                )
            # Crash after claiming, then recover only after its lease expires.
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.result_deletions set leased_until = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            lost = []

            async def proxy(request):
                response = await h.client.request(
                    request.method,
                    str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                if request.url.path.endswith("/complete_maintenance"):
                    lost.append(json.loads(request.content))
                    if len(lost) == 1 and response.is_success:
                        raise httpx.ReadTimeout(
                            "Cleanup committed; acknowledgement lost"
                        )
                return response

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(proxy)
            ) as transport:
                rpc = RpcClient(
                    h.connection["API_URL"],
                    h.connection["SECRET_KEY"],
                    schema="oshepherd_spike_api",
                    client=transport,
                )
                result = await ResultMaintenance(rpc, h.cluster, batch=1).cycle()
            assert len(lost) == 2 and lost[0] == lost[1]
            assert result["deleted"] == 1 and result["retrying"] == 0
            assert (await h.read(submission, job))["jobs"][0]["state"] == "succeeded"
            assert "0" in scoped_sql(
                h.cluster,
                "select count(*) from oshepherd_spike.result_deletions where cluster_id = :'cluster_id'::uuid;",
            )
            with pytest.raises(RpcError) as denied:
                await worker["rpc"].rpc(
                    "maintenance_tick", {"cluster_id": h.cluster, "limit": 1}
                )
            assert denied.value.status >= 400

    asyncio.run(run())


def test_orphan_scan_is_bounded_and_preserves_live_committed_and_other_cluster_objects(
    local_connection,
):
    async def run():
        async with queue_harness(local_connection, workers=2) as h:
            w0, w1 = h.workers
            _, orphan_job, orphan_claim, orphan = await uploaded(h, w0)
            await h.admin.rpc(
                "cancel_job",
                {
                    **h.caller(),
                    "job_id": orphan_job["job_id"],
                    "consumer_token": None,
                    "reason": "cancelled",
                },
            )
            committed_submission, committed_job, committed_claim, committed = (
                await uploaded(h, w1)
            )
            await w1["rpc"].rpc(
                "finish_job",
                {
                    **h.attempt(w1, committed_claim),
                    "final_chunks": [],
                    "result": committed,
                },
            )
            _, active_job, active_claim, active = await uploaded(h, w1)
            # Fault injection changes only creation timestamps under this
            # generated cluster prefix; bytes still came through real Storage.
            scoped_sql(
                h.cluster,
                "update storage.objects set created_at = clock_timestamp()-interval '20 minutes' where bucket_id = 'oshepherd-spike-results' and name like :'cluster_id' || '/%';",
            )
            ticks = [
                await h.admin.rpc(
                    "maintenance_tick", {"cluster_id": h.cluster, "limit": 1}
                )
                for _ in range(4)
            ]
            assert sum(item["scanned"] for item in ticks) == 3
            assert sum(item["enqueued"] for item in ticks) == 1
            assert all(
                item["scanned"] <= 1 and not item["object_paths"] for item in ticks
            )
            make_deletion_due(h)
            async with queue_harness(local_connection, workers=1) as other:
                _, other_job, _, other_descriptor = await uploaded(
                    other, other.workers[0]
                )
                await other.admin.rpc(
                    "cancel_job",
                    {
                        **other.caller(),
                        "job_id": other_job["job_id"],
                        "consumer_token": None,
                        "reason": "cancelled",
                    },
                )
                scoped_sql(
                    other.cluster,
                    "insert into oshepherd_spike.result_deletions(cluster_id,object_path,available_at) select :'cluster_id'::uuid,name,clock_timestamp()-interval '1 second' from storage.objects where bucket_id = 'oshepherd-spike-results' and name like :'cluster_id' || '/%';",
                )
                await h.claim(
                    w1,
                    active_attempts=[
                        {
                            "job_id": active_job["job_id"],
                            "attempt_id": active_claim["attempt_id"],
                        }
                    ],
                    max_jobs=0,
                )
                result = await ResultMaintenance(h.admin, h.cluster, batch=1).cycle()
                assert result["deleted"] == 1
                response = await other.client.get(
                    WorkerResultStorage(other.workers[0]["rpc"]).url(
                        other_descriptor["object_path"]
                    ),
                    headers=other.admin.headers(),
                )
                assert response.status_code == 200
            assert (await h.read(committed_submission, committed_job))["jobs"][0][
                "state"
            ] == "succeeded"
            for descriptor in (committed, active):
                response = await h.client.get(
                    WorkerResultStorage(w1["rpc"]).url(descriptor["object_path"]),
                    headers=h.admin.headers(),
                )
                assert response.status_code == 200
            missing = await h.client.get(
                WorkerResultStorage(w0["rpc"]).url(orphan["object_path"]),
                headers=h.admin.headers(),
            )
            assert missing.status_code >= 400
            # Cross-cluster paths are rejected before any mutation, even for
            # the privileged maintenance RPC identity.
            with pytest.raises(RpcError, match="invalid_request"):
                await h.admin.rpc(
                    "complete_maintenance",
                    {
                        "cluster_id": h.cluster,
                        "claim_id": str(uuid.uuid4()),
                        "deleted_paths": [
                            str(uuid.uuid4())
                            + "/"
                            + active_job["job_id"]
                            + "/"
                            + active_claim["attempt_id"]
                            + "/result.json"
                        ],
                        "failed_paths": [],
                    },
                )

    asyncio.run(run())


def test_large_expired_stream_purges_in_finite_batches_and_keeps_its_tombstone(
    local_connection,
):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            submission = h.submission()
            job = await h.admin.rpc("submit_job", submission)
            attempt = h.attempt(worker, (await h.claim(worker))["jobs"][0])
            content = '{"done":false}\n'
            for seq in range(1, 131):
                await worker["rpc"].rpc(
                    "append_output",
                    {
                        **attempt,
                        "seq": seq,
                        "content": content,
                        "checksum": checksum(content),
                    },
                )
            await worker["rpc"].rpc(
                "finish_job",
                {**attempt, "final_chunks": ['{"done":true}\n'], "result": None},
            )
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set content_expires_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            for remaining in (67, 3, 0):
                await h.admin.rpc(
                    "purge_expired", {"cluster_id": h.cluster, "limit": 1}
                )
                count = scoped_sql(
                    h.cluster,
                    "select count(*) from oshepherd_spike.chunks where job_id in(select id from oshepherd_spike.jobs where cluster_id = :'cluster_id'::uuid);",
                )
                assert str(remaining) in count
            tombstone = await h.admin.rpc("submit_job", submission)
            assert (
                tombstone["job_id"] == job["job_id"]
                and tombstone["state"] == "succeeded"
            )
            assert not tombstone["content_available"]
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set tombstone_expires_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            for _ in range(3):
                await h.admin.rpc(
                    "purge_expired", {"cluster_id": h.cluster, "limit": 1}
                )
            with pytest.raises(RpcError, match="not_found"):
                await h.read(submission, job)

    asyncio.run(run())


def test_database_cron_reaps_and_purges_without_gateway_or_worker_processes(
    local_connection,
):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            submission = h.submission(stream=False, mode="async")
            job = await h.admin.rpc("submit_job", submission)
            # Only the generated cluster's deadlines are fault-injected.
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set execution_deadline = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            scoped_sql(
                h.cluster,
                "create extension if not exists pg_cron; "
                "select cron.schedule(:'cluster_id','1 second',"
                "$$set statement_timeout = '5s'; set lock_timeout = '100ms'; "
                "select oshepherd_spike.scheduled_sweep('reap'); "
                "select oshepherd_spike.scheduled_sweep('purge');$$);",
            )
            try:
                async with asyncio.timeout(20):
                    while (await h.read(submission, job))["jobs"][0][
                        "state"
                    ] != "expired":
                        await asyncio.sleep(0.25)
                scoped_sql(
                    h.cluster,
                    "update oshepherd_spike.jobs set content_expires_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
                )
                async with asyncio.timeout(20):
                    while (
                        "t"
                        not in scoped_sql(
                            h.cluster,
                            "select bool_and(request = '{}'::jsonb) as purged from oshepherd_spike.jobs where cluster_id = :'cluster_id'::uuid;",
                        ).split()
                    ):
                        await asyncio.sleep(0.25)
                assert (
                    "t"
                    in scoped_sql(
                        h.cluster,
                        "select bool_and(not has_function_privilege(r, 'oshepherd_spike.scheduled_sweep(text)', 'EXECUTE')) from unnest(array['anon','authenticated','service_role']) r;",
                    ).split()
                )
                assert "succeeded" in scoped_sql(
                    h.cluster,
                    "select d.status from cron.job_run_details d join cron.job j using(jobid) where j.jobname = :'cluster_id' and d.status = 'succeeded' limit 1;",
                )
            finally:
                scoped_sql(
                    h.cluster,
                    "delete from cron.job_run_details where jobid in(select jobid from cron.job where jobname = :'cluster_id' and username = current_user); "
                    "select cron.unschedule(jobid) from cron.job where jobname = :'cluster_id' and username = current_user;",
                )

    asyncio.run(run())


def test_cron_schedule_replays_without_duplicates_and_refuses_command_collisions(
    local_connection, monkeypatch
):
    async def run():
        async with queue_harness(local_connection, workers=0) as h:
            # Exercise the operator SQL against only a generated job name.
            monkeypatch.setattr(
                supabase_local, "SCHEDULES", ((h.cluster, "1 second", "select 1;"),)
            )
            install = (
                "begin;\n"
                + "\n".join(supabase_local.schedule_statements())
                + "\ncommit;"
            )
            remove = (
                "begin;\n"
                + "\n".join(supabase_local.schedule_statements(remove=True))
                + "\ncommit;"
            )
            scoped_sql(h.cluster, install)
            try:
                query = "select jobid from cron.job where jobname = :'cluster_id' and username = current_user;"
                first = scoped_sql(h.cluster, query)
                scoped_sql(h.cluster, install)
                assert scoped_sql(h.cluster, query) == first
                scoped_sql(
                    h.cluster,
                    "select cron.schedule(:'cluster_id','1 second','select 2;');",
                )
                for mutation in (install, remove):
                    with pytest.raises(
                        AssertionError, match="Scoped development SQL failed"
                    ):
                        scoped_sql(h.cluster, mutation)
                    assert (
                        "t"
                        in scoped_sql(
                            h.cluster,
                            "select command = 'select 2;' from cron.job where jobname = :'cluster_id' and username = current_user;",
                        ).split()
                    )
            finally:
                # The test itself changed this exact command, so it owns cleanup
                # even though the operator correctly refused the collision.
                scoped_sql(
                    h.cluster,
                    "delete from cron.job_run_details where jobid in(select jobid from cron.job where jobname = :'cluster_id' and username = current_user); select cron.unschedule(jobid) from cron.job where jobname = :'cluster_id' and username = current_user;",
                )

    asyncio.run(run())
