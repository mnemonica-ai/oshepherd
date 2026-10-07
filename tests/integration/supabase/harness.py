"""Disposable real Auth/SQL identities bound only to the isolated local stack."""

from contextlib import asynccontextmanager
import os
import secrets
import subprocess
import uuid

import httpx

from oshepherd.backends.supabase.client import RpcClient
from oshepherd.backends.supabase.sessions import WorkerAuth
from .test_identity import cleanup_cluster


def scoped_sql(cluster, sql, **identifiers):
    """Checked-in SQL with UUID variables; no configurable database target."""
    command = [
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        "55322",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        "cluster_id=" + str(uuid.UUID(cluster)),
    ]
    for name, value in identifiers.items():
        command.extend(["-v", name + "=" + str(uuid.UUID(value))])
    response = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        env={**os.environ, "PGPASSWORD": "postgres"},
    )
    assert response.returncode == 0, "Scoped development SQL failed"
    return response.stdout


class QueueHarness:
    def __repr__(self):
        return "QueueHarness(credentials=[redacted])"

    async def setup(self, connection, client, *, workers=2):
        self.cluster = str(uuid.uuid4())
        self.subjects = []
        self.workers = []
        self.keys = []
        self.client = client
        self.connection = connection
        self.admin = RpcClient(
            connection["API_URL"],
            connection["SECRET_KEY"],
            schema="oshepherd_spike_api",
            client=client,
            sleep=lambda _: _no_sleep(),
        )
        owner, _ = await self.identity()
        await self.admin.rpc(
            "bootstrap_cluster", {"cluster_id": self.cluster, "owner_subject": owner}
        )
        for _ in range(2):
            key = "osk_" + secrets.token_urlsafe(32)
            await self.admin.rpc(
                "create_api_key",
                {
                    "cluster_id": self.cluster,
                    "key_id": str(uuid.uuid4()),
                    "raw_key": key,
                },
            )
            self.keys.append(key)
        for _ in range(workers):
            subject, rpc = await self.identity(signin=True)
            await self.admin.rpc(
                "enroll_worker",
                {"cluster_id": self.cluster, "auth_subject": subject, "capacity": 1},
            )
            registered = await rpc.rpc("register_worker", {})
            session = {name: registered[name] for name in ("session_id", "generation")}
            worker = {
                "rpc": rpc,
                "session": session,
                "worker_id": registered["worker_id"],
            }
            self.workers.append(worker)
            await self.models(worker)

    async def identity(self, signin=False):
        origin = self.connection["API_URL"]
        headers = {"apikey": self.connection["SECRET_KEY"]}
        email = f"queue-{uuid.uuid4()}@enrollment.invalid"
        response = await self.client.post(
            origin + "/auth/v1/admin/users",
            headers=headers,
            json={"email": email, "email_confirm": True},
        )
        assert response.status_code in (200, 201), "Disposable Auth identity failed"
        subject = response.json()["id"]
        self.subjects.append(subject)
        if not signin:
            return subject, None
        response = await self.client.post(
            origin + "/auth/v1/admin/generate_link",
            headers=headers,
            json={"type": "magiclink", "email": email},
        )
        assert response.status_code == 200
        session = await WorkerAuth(
            origin, self.connection["PUBLISHABLE_KEY"], client=self.client
        ).enroll(response.json()["hashed_token"])
        return subject, RpcClient(
            origin,
            self.connection["PUBLISHABLE_KEY"],
            access_token=session.access_token.get_secret_value(),
            schema="oshepherd_spike_api",
            client=self.client,
            sleep=lambda _: _no_sleep(),
        )

    async def models(self, worker, digest="a" * 64):
        return await worker["rpc"].rpc(
            "update_models",
            {
                **worker["session"],
                "models": [
                    {
                        "name": "test:latest",
                        "digest": digest,
                        "operations": ["generate", "chat", "embeddings", "systemone"],
                        "details": {"name": "test:latest", "digest": digest},
                        "show": {"model_info": {"architecture": "fake"}},
                    }
                ],
                "running": {"models": []},
                "version": {"version": "test"},
            },
        )

    def caller(self, index=0):
        return {"cluster_id": self.cluster, "key": self.keys[index]}

    def submission(self, *, stream=True, mode="sync", **extra):
        return {
            **self.caller(),
            "request": {
                "type": "generate",
                "payload": {"model": "test", "prompt": "hello", "stream": stream},
            },
            "mode": mode,
            "limits": {},
            "idempotency_key": None,
            "consumer_token": str(uuid.uuid4()) if mode == "sync" else None,
            "operation_id": str(uuid.uuid4()),
            **extra,
        }

    async def claim(self, worker, **extra):
        return await worker["rpc"].rpc(
            "heartbeat_and_claim",
            {
                **worker["session"],
                "active_attempts": [],
                "max_jobs": 1,
                "draining": False,
                **extra,
            },
        )

    def attempt(self, worker, claim):
        return {
            **worker["session"],
            **{key: claim[key] for key in ("job_id", "attempt_id")},
        }

    async def read(self, submission, job, *, cursor=0, **extra):
        return await self.admin.rpc(
            "read_updates",
            {
                **self.caller(),
                "cursors": [
                    {
                        "job_id": job["job_id"],
                        "cursor": cursor,
                        "consumer_token": submission["consumer_token"],
                    }
                ],
                "max_bytes": 262144,
                "max_chunks": 64,
                **extra,
            },
        )

    async def cleanup(self):
        cleanup_cluster(self.cluster)
        # Delete only objects beneath this test's generated cluster UUID after
        # removing its SQL references. Include uncommitted/lost-upload orphans.
        prefixes = [self.cluster]
        objects = []
        admin_headers = {"apikey": self.connection["SECRET_KEY"]}
        for _ in range(3):
            next_prefixes = []
            for prefix in prefixes:
                response = await self.client.post(
                    self.connection["API_URL"]
                    + "/storage/v1/object/list/oshepherd-spike-results",
                    headers=admin_headers,
                    json={"prefix": prefix, "limit": 1000, "offset": 0},
                )
                assert response.status_code == 200, "Scoped object listing failed"
                entries = response.json()
                assert (
                    len(entries) < 1000
                ), "Scoped object cleanup page exceeded its bound"
                for entry in entries:
                    name = entry["name"]
                    assert "/" not in name and name not in (
                        ".",
                        "..",
                    ), "Unexpected scoped object name"
                    path = prefix + "/" + name
                    if entry.get("id"):
                        objects.append(path)
                    else:
                        next_prefixes.append(path)
            prefixes = next_prefixes
            assert len(prefixes) <= 256, "Scoped object cleanup exceeded its bound"
        assert not prefixes, "Unexpected scoped object directory depth"
        for offset in range(0, len(objects), 100):
            response = await self.client.request(
                "DELETE",
                self.connection["API_URL"]
                + "/storage/v1/object/oshepherd-spike-results",
                headers=admin_headers,
                json={"prefixes": objects[offset : offset + 100]},
            )
            assert response.status_code == 200, "Scoped object cleanup failed"
        for subject in self.subjects:
            response = await self.client.delete(
                self.connection["API_URL"] + "/auth/v1/admin/users/" + subject,
                headers={"apikey": self.connection["SECRET_KEY"]},
            )
            assert response.status_code in (
                200,
                204,
            ), "Disposable Auth identity cleanup failed"


async def _no_sleep():
    pass


@asynccontextmanager
async def queue_harness(connection, **kwargs):
    async with httpx.AsyncClient(timeout=10) as client:
        harness = QueueHarness()
        try:
            await harness.setup(connection, client, **kwargs)
            yield harness
        finally:
            await harness.cleanup()
