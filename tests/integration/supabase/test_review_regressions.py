"""Review regressions exercised through real local Auth and PostgREST."""

import asyncio
import subprocess
import sys
import uuid

import pytest

from oshepherd.backends.supabase.client import RpcError
from .harness import queue_harness, scoped_sql
from .test_queue import inline


def scalar(h, query, **identifiers):
    return scoped_sql(
        h.cluster,
        "\\pset format unaligned\n\\pset tuples_only on\n" + query,
        **identifiers,
    ).splitlines()[-1]


@pytest.mark.parametrize(
    "prompts,expected",
    [
        (["x" * 800_000] * 3, 2),
        (["\\\n" * 200_000] * 3, 2),
        (["é😀" * 130_000] * 3, 2),
        (["x" * 1_047_000] * 3, 1),
        (["x" * 800_000, "small", "x" * 800_000], 3),
    ],
    ids=["ascii", "escaped", "unicode", "request-boundary", "mixed"],
)
def test_claim_pages_fit_transport_and_replay(local_connection, prompts, expected):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.workers set capacity=3 where cluster_id=:'cluster_id'::uuid;",
            )
            for prompt in prompts:
                submission = h.submission(mode="async", stream=False)
                submission["request"]["payload"]["prompt"] = prompt
                await h.admin.rpc("submit_job", submission)
            stopped = [
                {"job_id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
                for _ in range(64)
            ]
            request = {
                "operation_id": str(uuid.uuid4()),
                "max_jobs": 3,
                "active_attempts": stopped,
            }
            page = await h.claim(worker, **request)
            assert len(page["jobs"]) == expected
            assert page["stop"] == stopped
            assert await h.claim(worker, **request) == page
            # The real wire response, including its envelope, also fits the reader.
            response = await h.client.post(
                h.connection["API_URL"] + "/rest/v1/rpc/heartbeat_and_claim",
                headers=worker["rpc"].headers(),
                json={
                    "p_request": {
                        "v": 1,
                        **worker["session"],
                        "draining": False,
                        **request,
                    }
                },
            )
            assert (
                response.status_code == 200 and len(response.content) <= 2 * 1024 * 1024
            )
            assert scalar(
                h,
                "select count(*) from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid and state='queued';",
            ) == str(3 - expected)
            with pytest.raises(RpcError, match="idempotency_conflict"):
                await h.claim(worker, **{**request, "max_jobs": 2})
            remaining = await h.claim(worker, max_jobs=3)
            assert len(remaining["jobs"]) == 3 - expected
            assert len({job["job_id"] for job in page["jobs"] + remaining["jobs"]}) == 3

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_claim_receipts_do_not_retain_payload_after_purge(local_connection, mode):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.workers set capacity=2 where cluster_id=:'cluster_id'::uuid;",
            )
            submissions = [h.submission(mode=mode, stream=False) for _ in range(2)]
            jobs = [
                await h.admin.rpc("submit_job", submission)
                for submission in submissions
            ]
            operation = {"operation_id": str(uuid.uuid4()), "max_jobs": 2}
            claims = await h.claim(worker, **operation)
            assert len(claims["jobs"]) == 2
            assert await h.claim(worker, **operation) == claims
            assert (
                scalar(
                    h,
                    "select not exists(select 1 from oshepherd_spike.worker_operations o join oshepherd_spike.workers w on w.id=o.worker_id, lateral jsonb_array_elements(o.response #> '{data,jobs}') c where w.cluster_id=:'cluster_id'::uuid and o.kind='heartbeat_and_claim' and c ? 'request');",
                )
                == "t"
            )
            finished = {
                **h.attempt(
                    worker,
                    next(c for c in claims["jobs"] if c["job_id"] == jobs[0]["job_id"]),
                ),
                "final_chunks": [],
                "result": inline('{"done":true}'),
                "operation_id": str(uuid.uuid4()),
            }
            await worker["rpc"].rpc("finish_job", finished)
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set content_expires_at=clock_timestamp()-interval '1 second' where cluster_id=:'cluster_id'::uuid and id=:'job_id'::uuid;",
                job_id=jobs[0]["job_id"],
            )
            assert (
                await h.admin.rpc(
                    "purge_expired", {"cluster_id": h.cluster, "limit": 1}
                )
            )["purged"] == 1
            assert (
                scalar(
                    h,
                    "select request='{}'::jsonb from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid and id=:'job_id'::uuid;",
                    job_id=jobs[0]["job_id"],
                )
                == "t"
            )
            assert (
                scalar(
                    h,
                    "select request<>'{}'::jsonb from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid and id=:'job_id'::uuid;",
                    job_id=jobs[1]["job_id"],
                )
                == "t"
            )
            assert await worker["rpc"].rpc("finish_job", finished) == {
                "state": "succeeded"
            }
            with pytest.raises(RpcError, match="stale_attempt"):
                await h.claim(worker, **operation)
            assert (await h.admin.rpc("submit_job", submissions[0]))["job_id"] == jobs[
                0
            ]["job_id"]
            # Content-free claim receipts do not block eventual tombstone removal.
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set tombstone_expires_at=clock_timestamp()-interval '1 second' where cluster_id=:'cluster_id'::uuid and id=:'job_id'::uuid;",
                job_id=jobs[0]["job_id"],
            )
            await h.admin.rpc("purge_expired", {"cluster_id": h.cluster, "limit": 1})
            assert (
                scalar(
                    h,
                    "select count(*) from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid;",
                )
                == "1"
            )

    asyncio.run(run())


def test_upgrade_scrubs_existing_claim_receipts_and_preserves_replay(local_connection):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            await h.admin.rpc("submit_job", h.submission(mode="async", stream=False))
            operation = {"operation_id": str(uuid.uuid4())}
            claim = await h.claim(worker, **operation)
            # Restore the old payload-bearing representation for this fixture.
            scoped_sql(
                h.cluster,
                """
                update oshepherd_spike.worker_operations o set response=jsonb_set(o.response,'{data,jobs,0,request}',j.request)
                from oshepherd_spike.jobs j where j.cluster_id=:'cluster_id'::uuid and o.worker_id=j.worker_id
                and o.operation_id=:'operation_id'::uuid and j.id=(o.response #>> '{data,jobs,0,job_id}')::uuid;
                """,
                operation_id=operation["operation_id"],
            )
            assert (
                scalar(
                    h,
                    "select response #> '{data,jobs,0}' ? 'request' from oshepherd_spike.worker_operations where operation_id=:'operation_id'::uuid and worker_id in(select id from oshepherd_spike.workers where cluster_id=:'cluster_id'::uuid);",
                    operation_id=operation["operation_id"],
                )
                == "t"
            )
            # Reapply the actual upgrade; it must preserve the receipt identity.
            upgrade = subprocess.run(
                [sys.executable, "-m", "scripts.supabase_local", "apply-spike"],
                capture_output=True,
                text=True,
            )
            assert (
                upgrade.returncode == 0
            ), "Isolated spike upgrade failed; inspect its protected log"
            assert (
                scalar(
                    h,
                    "select not(response #> '{data,jobs,0}' ? 'request') from oshepherd_spike.worker_operations where operation_id=:'operation_id'::uuid and worker_id in(select id from oshepherd_spike.workers where cluster_id=:'cluster_id'::uuid);",
                    operation_id=operation["operation_id"],
                )
                == "t"
            )
            assert await h.claim(worker, **operation) == claim

    asyncio.run(run())


def test_retry_queue_budget_is_capped_by_total_deadline(local_connection):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            submission = h.submission(mode="async", stream=False)
            job = await h.admin.rpc("submit_job", submission)
            await h.claim(h.workers[0])
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set queue_deadline=clock_timestamp()-interval '1 second', lease_until=clock_timestamp()-interval '1 second', execution_deadline=clock_timestamp()+interval '10 seconds' where cluster_id=:'cluster_id'::uuid;",
            )
            await h.admin.rpc("reap_jobs", {"cluster_id": h.cluster, "limit": 32})
            assert (await h.read(submission, job))["jobs"][0]["state"] == "queued"
            assert (
                scalar(
                    h,
                    "select queue_deadline=execution_deadline and available_at<execution_deadline from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid;",
                )
                == "t"
            )

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("cause", ["lease", "failure"])
def test_retry_uses_fresh_queue_budget_and_fences_old_attempt(
    local_connection, mode, cause
):
    async def run():
        async with queue_harness(local_connection) as h:
            worker, replacement = h.workers
            submission = h.submission(
                mode=mode, stream=False, limits={"queue_ms": 12345}
            )
            job = await h.admin.rpc("submit_job", submission)
            old = h.attempt(worker, (await h.claim(worker))["jobs"][0])
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set queue_deadline=clock_timestamp()-interval '15 seconds' where cluster_id=:'cluster_id'::uuid;",
            )
            if cause == "lease":
                scoped_sql(
                    h.cluster,
                    "update oshepherd_spike.jobs set lease_until=clock_timestamp()-interval '1 second' where cluster_id=:'cluster_id'::uuid;",
                )
                await h.admin.rpc("reap_jobs", {"cluster_id": h.cluster, "limit": 32})
            else:
                await worker["rpc"].rpc(
                    "fail_attempt",
                    {
                        **old,
                        "error": {
                            "code": "upstream_closed",
                            "message": "Upstream closed",
                        },
                        "retryable": True,
                    },
                )
            page = (await h.read(submission, job))["jobs"][0]
            assert page["state"] == "queued"
            assert page["execution_deadline"] == job["execution_deadline"]
            assert (
                scalar(
                    h,
                    "select queue_deadline>available_at and queue_deadline<=execution_deadline and abs(extract(epoch from(queue_deadline-available_at))*1000-12345)<1 from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid;",
                )
                == "t"
            )
            assert (await h.claim(replacement))["jobs"] == []  # backoff still enforced
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set available_at=clock_timestamp()-interval '1 second' where cluster_id=:'cluster_id'::uuid;",
            )
            next_claim = (await h.claim(replacement))["jobs"][0]
            assert (
                next_claim["job_id"] == job["job_id"]
                and next_claim["attempt_id"] != old["attempt_id"]
            )
            with pytest.raises(RpcError, match="stale_attempt"):
                await worker["rpc"].rpc(
                    "finish_job",
                    {**old, "final_chunks": [], "result": inline('{"done":true}')},
                )
            assert (
                scalar(
                    h,
                    "select attempts from oshepherd_spike.jobs where cluster_id=:'cluster_id'::uuid;",
                )
                == "2"
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "exclusion,expected",
    [
        ("queued", "expired"),
        ("total", "expired"),
        ("consumer", "cancelled"),
        ("output", "failed"),
        ("ceiling", "failed"),
    ],
)
def test_retry_exclusions_remain_terminal(local_connection, exclusion, expected):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            submission = h.submission(stream=True)
            job = await h.admin.rpc("submit_job", submission)
            if exclusion != "queued":
                await h.claim(h.workers[0])
            field = {
                "queued": "queue_deadline=clock_timestamp()-interval '1 second'",
                "total": "execution_deadline=clock_timestamp()-interval '1 second'",
                "consumer": "consumer_until=clock_timestamp()-interval '1 second'",
                "output": "output_bytes=1",
                "ceiling": "attempts=6",
            }[exclusion]
            scoped_sql(
                h.cluster,
                f"update oshepherd_spike.jobs set lease_until=clock_timestamp()-interval '1 second', {field} where cluster_id=:'cluster_id'::uuid;",
            )
            await h.admin.rpc("reap_jobs", {"cluster_id": h.cluster, "limit": 32})
            assert (await h.read(submission, job))["jobs"][0]["state"] == expected
            assert (await h.claim(h.workers[0]))["jobs"] == []

    asyncio.run(run())
