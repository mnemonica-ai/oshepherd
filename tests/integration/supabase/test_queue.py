"""Queue transactions through real Auth/PostgREST, including adversarial races."""

import asyncio
import hashlib
import json
import uuid

import pytest

from oshepherd.backends.supabase.client import RpcError
from .harness import queue_harness, scoped_sql


def checksum(content):
    return hashlib.sha256(content.encode()).hexdigest()


def inline(content):
    return {
        "inline": content,
        "object_path": None,
        "size_bytes": len(content.encode()),
        "checksum": checksum(content),
        "content_type": "application/json",
    }


def test_atomic_stream_replay_and_no_retry_after_bytes(local_connection):
    async def run():
        async with queue_harness(local_connection) as h:
            worker = h.workers[0]
            submission = h.submission(idempotency_key="stream-one")
            job = await h.admin.rpc("submit_job", submission)
            assert job == await h.admin.rpc("submit_job", submission)
            assert job == await h.admin.rpc(
                "submit_job", {**submission, "operation_id": str(uuid.uuid4())}
            )
            with pytest.raises(RpcError, match="idempotency_conflict"):
                await h.admin.rpc(
                    "submit_job", {**submission, "limits": {"max_output_bytes": 10}}
                )
            claim_request = {"operation_id": str(uuid.uuid4())}
            claim = await h.claim(worker, **claim_request)
            assert claim == await h.claim(worker, **claim_request)
            attempt = h.attempt(worker, claim["jobs"][0])
            record = '{"response": "héllo", "done": false}\n'
            append = {
                **attempt,
                "seq": 1,
                "content": record,
                "checksum": checksum(record),
                "operation_id": str(uuid.uuid4()),
            }
            assert await worker["rpc"].rpc("append_output", append) == {"seq": 1}
            assert await worker["rpc"].rpc("append_output", append) == {"seq": 1}
            assert await worker["rpc"].rpc(
                "append_output", {**append, "operation_id": str(uuid.uuid4())}
            ) == {"seq": 1}
            with pytest.raises(RpcError, match="sequence_gap"):
                await worker["rpc"].rpc(
                    "append_output",
                    {**append, "operation_id": str(uuid.uuid4()), "seq": 3},
                )
            with pytest.raises(RpcError, match="invalid_chunk"):
                await worker["rpc"].rpc(
                    "append_output",
                    {
                        **append,
                        "operation_id": str(uuid.uuid4()),
                        "seq": 2,
                        "checksum": "b" * 64,
                    },
                )
            other = h.workers[1]
            with pytest.raises(RpcError, match="stale_attempt"):
                await other["rpc"].rpc(
                    "append_output",
                    {**append, **other["session"], "operation_id": str(uuid.uuid4())},
                )
            page = (await h.read(submission, job))["jobs"][0]
            assert page["state"] == "running" and page["cursor"] == 1
            assert page["chunks"][0]["content"] == record
            final = '{"response": "", "done": true, "eval_count": 7}\n'
            with pytest.raises(RpcError, match="invalid_terminal_record"):
                await worker["rpc"].rpc(
                    "finish_job",
                    {**attempt, "final_chunks": ['{"done":false}\n'], "result": None},
                )
            assert (await h.read(submission, job, cursor=1))["jobs"][0][
                "state"
            ] == "running"
            finish = {
                **attempt,
                "final_chunks": [final],
                "result": None,
                "operation_id": str(uuid.uuid4()),
            }
            assert await worker["rpc"].rpc("finish_job", finish) == {
                "state": "succeeded"
            }
            assert await worker["rpc"].rpc("finish_job", finish) == {
                "state": "succeeded"
            }
            page = (await h.read(submission, job, cursor=1))["jobs"][0]
            assert page["state"] == "succeeded" and page["cursor"] == 2
            assert page["chunks"][0]["content"] == final
            assert page["output_bytes"] == len((record + final).encode())
            # A cancelled reader racing after commit cannot replace success.
            cancelled = await h.admin.rpc(
                "cancel_job",
                {
                    **h.caller(),
                    "job_id": job["job_id"],
                    "consumer_token": submission["consumer_token"],
                    "reason": "cancelled",
                },
            )
            assert cancelled["state"] == "succeeded"
            # An upstream failure after durable output must never enqueue retry.
            submission2 = h.submission()
            job2 = await h.admin.rpc("submit_job", submission2)
            attempt2 = h.attempt(worker, (await h.claim(worker))["jobs"][0])
            await worker["rpc"].rpc(
                "append_output",
                {**attempt2, "seq": 1, "content": record, "checksum": checksum(record)},
            )
            failed = await worker["rpc"].rpc(
                "fail_attempt",
                {
                    **attempt2,
                    "error": {"code": "upstream_closed", "message": "Upstream closed"},
                    "retryable": True,
                },
            )
            assert failed["state"] == "failed"
            assert (await h.read(submission2, job2))["jobs"][0]["state"] == "failed"
            assert (await h.claim(other))["jobs"] == []

    asyncio.run(run())


def test_claim_race_capacity_and_session_fencing(local_connection):
    async def run():
        async with queue_harness(local_connection) as h:
            submissions = [h.submission(stream=False) for _ in range(4)]
            jobs = await asyncio.gather(
                *(h.admin.rpc("submit_job", request) for request in submissions)
            )
            with pytest.raises(RpcError, match="capacity_exceeded"):
                await h.admin.rpc("submit_job", h.submission())
            claims = await asyncio.gather(*(h.claim(worker) for worker in h.workers))
            ids = [claim["jobs"][0]["job_id"] for claim in claims]
            assert len(set(ids)) == 2
            assert all(job_id in {job["job_id"] for job in jobs} for job_id in ids)
            assert all(
                not claim["jobs"]
                for claim in await asyncio.gather(
                    *(h.claim(worker) for worker in h.workers)
                )
            )
            worker = h.workers[0]
            active = {
                key: claims[0]["jobs"][0][key] for key in ("job_id", "attempt_id")
            }
            renewal = await h.claim(worker, active_attempts=[active], max_jobs=0)
            assert renewal["stop"] == []
            old = h.attempt(worker, claims[0]["jobs"][0])
            new = await worker["rpc"].rpc("register_worker", {})
            with pytest.raises(RpcError, match="stale_session"):
                await worker["rpc"].rpc(
                    "finish_job",
                    {**old, "final_chunks": [], "result": inline('{"done":true}')},
                )
            worker["session"] = {key: new[key] for key in ("generation", "session_id")}
            reaped = await h.admin.rpc(
                "reap_jobs", {"cluster_id": h.cluster, "limit": 128}
            )
            assert reaped["reaped"] == 1
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set available_at = clock_timestamp()-interval '1 second' where cluster_id = :'cluster_id'::uuid;",
            )
            # The new generation can receive the retried job, with a new attempt.
            replacement = (await h.claim(worker))["jobs"][0]
            assert replacement["attempt_id"] != active["attempt_id"]
            with pytest.raises(RpcError, match="stale_attempt"):
                await worker["rpc"].rpc(
                    "finish_job",
                    {
                        **old,
                        **worker["session"],
                        "final_chunks": [],
                        "result": inline('{"done":true}'),
                    },
                )

    asyncio.run(run())


def test_application_key_isolation_and_metadata(local_connection):
    async def run():
        async with queue_harness(local_connection) as h:
            submission = h.submission(stream=False, mode="async")
            job = await h.admin.rpc("submit_job", submission)
            with pytest.raises(RpcError, match="not_found"):
                await h.read(submission, job, key=h.keys[1])
            with pytest.raises(RpcError, match="unauthorized"):
                await h.read(submission, job, key="invalid")
            with pytest.raises(RpcError):
                await h.workers[0]["rpc"].rpc("submit_job", submission)
            for operation, expected in [
                ("tags", {"models": [{"name": "test:latest", "digest": "a" * 64}]}),
                ("ps", {"models": []}),
                ("version", {"version": "test"}),
                ("show", {"model_info": {"architecture": "fake"}}),
            ]:
                result = await h.admin.rpc(
                    "read_metadata",
                    {
                        **h.caller(),
                        "operation": operation,
                        "model": "test" if operation == "show" else None,
                    },
                )
                assert result["response"] == expected
            await h.models(h.workers[1], digest="b" * 64)
            with pytest.raises(RpcError, match="model_digest_conflict"):
                await h.admin.rpc("submit_job", h.submission())
            with pytest.raises(RpcError, match="async_streaming_unsupported"):
                await h.admin.rpc("submit_job", h.submission(mode="async"))
            # Worker role has no direct table privileges or private helpers.
            response = await h.client.get(
                h.connection["API_URL"] + "/rest/v1/jobs",
                headers={
                    **h.workers[0]["rpc"].headers(),
                    "Accept-Profile": "oshepherd_spike",
                },
            )
            assert response.status_code >= 400

    asyncio.run(run())


def test_inline_result_expiry_and_consumer_cancellation(local_connection):
    async def run():
        async with queue_harness(local_connection) as h:
            worker = h.workers[0]
            submission = h.submission(stream=False, mode="async")
            job = await h.admin.rpc("submit_job", submission)
            attempt = h.attempt(worker, (await h.claim(worker))["jobs"][0])
            content = json.dumps({"done": True, "response": "hello"})
            finish = {
                **attempt,
                "final_chunks": [],
                "result": inline(content),
                "operation_id": str(uuid.uuid4()),
            }
            invalid = {
                **finish,
                "operation_id": str(uuid.uuid4()),
                "result": {**inline(content), "size_bytes": 1},
            }
            with pytest.raises(RpcError, match="invalid_result"):
                await worker["rpc"].rpc("finish_job", invalid)
            assert (await h.read(submission, job))["jobs"][0]["state"] == "running"
            await worker["rpc"].rpc("finish_job", finish)
            assert await worker["rpc"].rpc("finish_job", finish) == {
                "state": "succeeded"
            }
            assert (await h.read(submission, job))["jobs"][0]["result"][
                "inline"
            ] == content
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set content_expires_at = clock_timestamp()-interval '1 second' where id = :'job_id'::uuid and cluster_id = :'cluster_id'::uuid;",
                job_id=job["job_id"],
            )
            page = (await h.read(submission, job))["jobs"][0]
            assert (
                page["state"] == "succeeded"
                and not page["content_available"]
                and page["result"] is None
            )
            assert (
                await h.admin.rpc(
                    "purge_expired", {"cluster_id": h.cluster, "limit": 128}
                )
            )["purged"] == 1
            # Replay returns the retained tombstone, rather than a new inference.
            assert (await h.admin.rpc("submit_job", submission))["job_id"] == job[
                "job_id"
            ]
            sync = h.submission()
            sync_job = await h.admin.rpc("submit_job", sync)
            with pytest.raises(RpcError, match="forbidden"):
                await h.read({**sync, "consumer_token": str(uuid.uuid4())}, sync_job)
            scoped_sql(
                h.cluster,
                "update oshepherd_spike.jobs set consumer_until = clock_timestamp()-interval '1 second' where id = :'job_id'::uuid and cluster_id = :'cluster_id'::uuid;",
                job_id=sync_job["job_id"],
            )
            page = (await h.read(sync, sync_job))["jobs"][0]
            assert (
                page["state"] == "cancelled"
                and page["error"]["code"] == "consumer_expired"
            )

    asyncio.run(run())
