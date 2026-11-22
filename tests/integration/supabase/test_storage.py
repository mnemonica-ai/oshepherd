import asyncio
import hashlib
import json

import pytest

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.storage import WorkerResultStorage
from .harness import queue_harness, scoped_sql


@pytest.mark.parametrize("rpc_schema", ["oshepherd_spike_api", "oshepherd_api"])
def test_private_immutable_object_and_authoritative_commit(
    local_connection, request, rpc_schema
):
    if rpc_schema == "oshepherd_api":
        request.getfixturevalue("production_schema")

    async def run():
        async with queue_harness(local_connection, schema=rpc_schema) as h:
            worker, other = h.workers
            submission = h.submission(stream=False, mode="async")
            job = await h.admin.rpc("submit_job", submission)
            claim = (await h.claim(worker))["jobs"][0]
            fields = h.attempt(worker, claim)
            path = (
                "/".join((h.cluster, claim["job_id"], claim["attempt_id"]))
                + "/result.json"
            )
            content = json.dumps({"done": True, "response": "x" * 300000}).encode()
            descriptor = {
                "inline": None,
                "object_path": path,
                "size_bytes": len(content),
                "checksum": hashlib.sha256(content).hexdigest(),
                "content_type": "application/json",
            }
            finish = {**fields, "final_chunks": [], "result": descriptor}
            with pytest.raises(RpcError, match="result_object_missing"):
                await worker["rpc"].rpc("finish_job", finish)
            storage = WorkerResultStorage(worker["rpc"])
            url = storage.url(path)
            forbidden = await h.client.post(
                url,
                content=content,
                headers={**other["rpc"].headers(), "Content-Type": "application/json"},
            )
            assert forbidden.status_code >= 400
            uploaded = await storage.upload(h.cluster, claim, content)
            assert uploaded == descriptor
            assert await storage.upload(h.cluster, claim, content) == descriptor
            if rpc_schema == "oshepherd_api":
                # Versioned Storage can archive an object with a non-key
                # UPDATE. The commit trigger must fence that, not only DELETE.
                from types import SimpleNamespace
                from uuid import uuid4
                from pydantic import SecretStr
                from oshepherd.backends.supabase.releases import Database, OperatorError

                db = Database(
                    SimpleNamespace(
                        project_ref="local",
                        database_url=SecretStr(
                            "postgresql://supabase_admin:postgres@127.0.0.1:55322/postgres"
                        ),
                    )
                )
                marker = "storage-lock-" + str(uuid4())
                update = f"update storage.objects set metadata=metadata where bucket_id='oshepherd-results' and name='{path}';"
                db.execute(update)
                locked = asyncio.create_task(
                    asyncio.to_thread(
                        db.execute,
                        f"begin; set local application_name='{marker}'; set local role oshepherd_runtime; "
                        f"insert into oshepherd.result_objects(job_id,bucket_id,object_path) values('{claim['job_id']}','oshepherd-results','{path}'); "
                        "select pg_sleep(5); rollback;",
                    )
                )
                try:
                    async with asyncio.timeout(3):
                        while (
                            await asyncio.to_thread(
                                db.execute,
                                f"select count(*) from pg_stat_activity where application_name='{marker}' and wait_event='PgSleep';",
                            )
                            != "1"
                        ):
                            if locked.done():
                                await locked
                                pytest.fail(
                                    "commit trigger did not reach held transaction"
                                )
                            await asyncio.sleep(0.05)
                    with pytest.raises(OperatorError, match="migration_failed"):
                        await asyncio.to_thread(
                            db.execute, "set lock_timeout='100ms'; " + update
                        )
                finally:
                    await locked
                db.execute(update)
            with pytest.raises(RpcError, match="result_object_conflict"):
                await storage.upload(h.cluster, claim, content + b" ")
            public = await h.client.get(url)
            assert public.status_code >= 400
            # The authenticated role cannot replace an immutable upload.
            overwrite = await h.client.post(
                url,
                content=b"{}",
                headers={
                    **worker["rpc"].headers(),
                    "Content-Type": "application/json",
                    "x-upsert": "true",
                },
            )
            assert overwrite.status_code >= 400
            await worker["rpc"].rpc("finish_job", finish)
            page = (await h.read(submission, job))["jobs"][0]
            assert page["state"] == "succeeded" and page["result"] == descriptor
            assert not page["result_omitted"]
            # Once committed, the old attempt loses its worker read/upload grant.
            assert (
                await h.client.get(url, headers=worker["rpc"].headers())
            ).status_code >= 400
            scoped_sql(
                h.cluster,
                f"update {'oshepherd' if rpc_schema == 'oshepherd_api' else 'oshepherd_spike'}.jobs set content_expires_at = clock_timestamp()-interval '1 second' where id = :'job_id'::uuid and cluster_id = :'cluster_id'::uuid;",
                job_id=job["job_id"],
            )
            purged = await h.admin.rpc(
                "purge_expired", {"cluster_id": h.cluster, "limit": 128}
            )
            assert purged["object_paths"] == [path]
            removed = await h.client.request(
                "DELETE",
                h.connection["API_URL"] + "/storage/v1/object/" + h.bucket,
                headers={"apikey": h.connection["SECRET_KEY"]},
                json={"prefixes": [path]},
            )
            assert removed.status_code == 200

    asyncio.run(run())


def test_cancelled_attempt_cannot_upload_object(local_connection):
    async def run():
        async with queue_harness(local_connection, workers=1) as h:
            worker = h.workers[0]
            submission = h.submission(stream=False, mode="async")
            job = await h.admin.rpc("submit_job", submission)
            claim = (await h.claim(worker))["jobs"][0]
            await h.admin.rpc(
                "cancel_job",
                {
                    **h.caller(),
                    "job_id": job["job_id"],
                    "consumer_token": None,
                    "reason": "cancelled",
                },
            )
            with pytest.raises(RpcError, match="storage_write_denied"):
                await WorkerResultStorage(worker["rpc"]).upload(
                    h.cluster, claim, b'{"done":true}'
                )

    asyncio.run(run())
