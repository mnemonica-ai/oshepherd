"""Persistent, local-only Edge -> external worker -> installed Ollama model demo.

Run from the checkout with ``python -m scripts.supabase_demo setup --model ...``.
No cloud target, model download, SQL migration or deletion is performed here.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import subprocess
import sys
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from oshepherd.backends.supabase.client import RpcClient, RpcError
from oshepherd.common.protocol import RECORD_BYTES, RESULT_BYTES
from oshepherd.backends.supabase.config import (
    SupabaseWorkerConfig,
    SupabaseMaintenanceConfig,
)
from oshepherd.backends.supabase.maintenance import run_maintenance
from oshepherd.backends.supabase.private_files import (
    private_directory,
    read_private_json,
    write_private,
    write_private_json,
)
from oshepherd.backends.supabase.sessions import WorkerAuth, WorkerSession
from scripts.local_runtime import ROOT, runtime_lock, stop_process

ORIGIN = "http://127.0.0.1:55321"
OLLAMA = "http://127.0.0.1:11434"
GATEWAY = ORIGIN + "/functions/v1/ollama"
STATE = ROOT / ".oshepherd/local/demo"


class DemoState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = 1
    supabase_url: Literal[ORIGIN] = ORIGIN
    cluster_id: UUID = Field(default_factory=uuid4)
    key_id: UUID = Field(default_factory=uuid4)
    bootstrap_operation: UUID = Field(default_factory=uuid4)
    key_operation: UUID = Field(default_factory=uuid4)
    enroll_operation: UUID = Field(default_factory=uuid4)
    application_key: SecretStr = Field(
        default_factory=lambda: SecretStr("osk_" + secrets.token_urlsafe(32)),
        repr=False,
    )
    owner_subject: UUID | None = None
    worker_subject: UUID | None = None
    worker_id: UUID | None = None
    model: str
    exchanging_enrollment: bool = False

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value):
            raise ValueError("Invalid local model name")
        return value

    @field_validator("application_key")
    @classmethod
    def validate_key(cls, value):
        if not re.fullmatch(r"osk_[A-Za-z0-9_-]{43}", value.get_secret_value()):
            raise ValueError("Invalid application key")
        return value

    def save(self, directory):
        value = self.model_dump(mode="json")
        value["application_key"] = self.application_key.get_secret_value()
        write_private_json(directory / "state.json", value)

    @classmethod
    def load(cls, directory):
        value = read_private_json(directory / "state.json")
        # Defaults are for a new install only. A damaged journal must never
        # silently generate replacement identities, nonces or key material.
        if not isinstance(value, dict) or set(value) != set(cls.model_fields):
            raise ValueError("Incomplete local installation journal")
        return cls.model_validate(value)


async def bounded_json(client, method, url, *, limit=1048576, **kwargs):
    try:
        async with client.stream(method, url, **kwargs) as response:
            if response.is_error:
                raise RpcError("local_service_error", response.status_code)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > limit:
                    raise RpcError("local_response_too_large")
                content.extend(chunk)
            return json.loads(content)
    except httpx.TransportError:
        raise RpcError("local_service_unavailable") from None
    except ValueError:
        raise RpcError("invalid_local_response") from None


def connection():
    value = read_private_json(ROOT / ".oshepherd/local/connection.json")
    if value.get("API_URL") != ORIGIN:
        raise ValueError("Only the isolated local stack is accepted")
    # Validate the worker credential before writing any environment files.
    SupabaseWorkerConfig(
        SUPABASE_URL=ORIGIN, SUPABASE_PUBLISHABLE_KEY=value["PUBLISHABLE_KEY"]
    )
    return value


async def installed_model(client, model):
    value = await bounded_json(client, "GET", OLLAMA + "/api/tags")
    expected = model if ":" in model else model + ":latest"
    found = next(
        (item for item in value.get("models", []) if item.get("name") == expected), None
    )
    if found is None:
        raise RpcError("local_model_not_installed", 404)
    details = await bounded_json(
        client, "POST", OLLAMA + "/api/show", json={"model": model}
    )
    if "completion" not in details.get("capabilities", []):
        raise RpcError("local_model_cannot_generate", 422)
    return expected


async def ensure_subject(client, headers, state, field, directory):
    subject = getattr(state, field)
    email = f"oshepherd-demo-{state.cluster_id}-{field}@enrollment.invalid"
    if subject:
        value = await bounded_json(
            client,
            "GET",
            ORIGIN + "/auth/v1/admin/users/" + str(subject),
            headers=headers,
        )
        if value.get("email") != email:
            raise RpcError("local_identity_mismatch")
        return str(subject)
    # Reconcile an ambiguous creation instead of issuing another Auth identity.
    # The persisted random cluster UUID names this local install uniquely.
    for page in range(1, 101):
        result = await bounded_json(
            client,
            "GET",
            ORIGIN + "/auth/v1/admin/users",
            headers=headers,
            params={"page": page, "per_page": 100},
        )
        users = result.get("users")
        if not isinstance(users, list):
            raise RpcError("invalid_local_response")
        matches = [item for item in users if item.get("email") == email]
        if matches:
            if len(matches) != 1:
                raise RpcError("local_identity_conflict")
            subject = UUID(matches[0]["id"])
            break
        if len(users) < 100:
            value = await bounded_json(
                client,
                "POST",
                ORIGIN + "/auth/v1/admin/users",
                headers=headers,
                json={"email": email, "email_confirm": True},
            )
            subject = UUID(value["id"])
            break
    else:
        raise RpcError("local_identity_scan_limit")
    setattr(state, field, subject)
    state.save(directory)
    return str(subject)


async def setup(client, local, *, model, directory=STATE, recover_enrollment=False):
    directory = private_directory(directory)
    filename = directory / "state.json"
    if filename.exists():
        state = DemoState.load(directory)
        if model is not None and model != state.model:
            raise ValueError("Existing demo uses another model; keep its saved model")
    else:
        if model is None:
            raise ValueError("First setup requires --model with an installed model")
        state = DemoState(model=model)
        state.save(directory)  # IDs and key survive acknowledgement loss.
    await installed_model(client, state.model)
    rpc = RpcClient(
        ORIGIN, local["SECRET_KEY"], schema="oshepherd_spike_api", client=client
    )
    headers = rpc.headers()
    owner = await ensure_subject(client, headers, state, "owner_subject", directory)
    await rpc.rpc(
        "bootstrap_cluster",
        {
            "cluster_id": str(state.cluster_id),
            "owner_subject": owner,
            "operation_id": str(state.bootstrap_operation),
        },
    )
    await rpc.rpc(
        "create_api_key",
        {
            "cluster_id": str(state.cluster_id),
            "key_id": str(state.key_id),
            "raw_key": state.application_key.get_secret_value(),
            "operation_id": str(state.key_operation),
        },
    )
    worker = await ensure_subject(client, headers, state, "worker_subject", directory)
    bound = await rpc.rpc(
        "enroll_worker",
        {
            "cluster_id": str(state.cluster_id),
            "auth_subject": worker,
            "capacity": 1,
            "operation_id": str(state.enroll_operation),
        },
    )
    state.worker_id = UUID(bound["worker_id"])
    state.save(directory)
    session_file = directory / "worker-session.json"
    if session_file.exists():
        session = WorkerSession.load(session_file, supabase_url=ORIGIN)
        if session.user_id != state.worker_subject:
            raise ValueError("Demo session belongs to another worker")
    else:
        if state.exchanging_enrollment and not recover_enrollment:
            raise RpcError("enrollment_exchange_ambiguous")
        link = await bounded_json(
            client,
            "POST",
            ORIGIN + "/auth/v1/admin/generate_link",
            headers=headers,
            json={
                "type": "magiclink",
                "email": f"oshepherd-demo-{state.cluster_id}-worker_subject@enrollment.invalid",
            },
        )
        # Never replay a one-time verification after an ambiguous response.
        state.exchanging_enrollment = True
        state.save(directory)
        session = await WorkerAuth(
            ORIGIN, local["PUBLISHABLE_KEY"], client=client
        ).enroll(link["hashed_token"])
        if session.user_id != state.worker_subject:
            raise ValueError("Enrollment returned another worker")
        session.save(session_file)
    state.exchanging_enrollment = False
    state.save(directory)
    write_private(
        directory / "worker.env",
        "\n".join(
            [
                "SUPABASE_URL=" + ORIGIN,
                "SUPABASE_PUBLISHABLE_KEY=" + local["PUBLISHABLE_KEY"],
                "SUPABASE_SCHEMA=oshepherd_spike_api",
                "WORKER_SESSION_FILE=" + json.dumps(str(session_file)),
                "OLLAMA_BASE_URL=" + OLLAMA,
                "CONCURRENCY=1",
                "HEARTBEAT_SECONDS=1",
                "DRAIN_SECONDS=20",
            ]
        )
        + "\n",
    )
    write_private(
        directory / "edge.env",
        f"OSHEPHERD_CLUSTER_ID={state.cluster_id}\nOSHEPHERD_SCHEMA=oshepherd_spike_api\nOSHEPHERD_PUBLIC_URL={ORIGIN}\n",
    )
    write_private_json(
        directory / "client.json",
        {
            "v": 1,
            "gateway_url": GATEWAY,
            "application_key": state.application_key.get_secret_value(),
            "model": state.model,
        },
    )
    return state


async def doctor(client, state):
    expected = await installed_model(client, state.model)
    rpc = RpcClient(
        ORIGIN, connection()["SECRET_KEY"], schema="oshepherd_spike_api", client=client
    )
    caller = {
        "cluster_id": str(state.cluster_id),
        "key": state.application_key.get_secret_value(),
    }
    info = await rpc.rpc("backend_info", caller)
    if info.get("protocol") != 1 or info.get("schema") != "oshepherd_spike_api":
        raise RpcError("protocol_mismatch")
    response = await bounded_json(
        client,
        "GET",
        GATEWAY + "/health",
        headers={"Authorization": "Bearer " + caller["key"]},
    )
    if response != {"status": 200}:
        raise RpcError("local_gateway_unavailable")
    response = await bounded_json(
        client,
        "GET",
        GATEWAY + "/api/tags",
        headers={"Authorization": "Bearer " + caller["key"]},
    )
    if not any(item.get("name") == expected for item in response.get("models", [])):
        raise RpcError("local_worker_offline")
    return {"gateway": "ready", "worker": "connected", "model": expected, "protocol": 1}


async def serve(client, state):
    async with runtime_lock():
        processes, logs = [], []
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        for signum in signals:
            loop.add_signal_handler(signum, stop.set)
        try:
            env = dict(os.environ)
            # This profile is fixed; an ambient worker URL/session/admin key
            # must not override the generated, backend-specific configuration.
            for name in tuple(env):
                if (
                    name.startswith(("SUPABASE_", "OSHEPHERD_"))
                    or name in SupabaseWorkerConfig.model_fields
                ):
                    env.pop(name)
            for name, command in (
                (
                    "worker",
                    [
                        sys.executable,
                        "-m",
                        "oshepherd.cli",
                        "start-worker",
                        "--backend",
                        "supabase",
                        "--env-file",
                        str(STATE / "worker.env"),
                    ],
                ),
                (
                    "edge",
                    [
                        "supabase",
                        "functions",
                        "serve",
                        "ollama",
                        "--env-file",
                        str(STATE / "edge.env"),
                        "--no-verify-jwt",
                    ],
                ),
                (
                    "maintenance",
                    [sys.executable, "-m", "scripts.supabase_demo", "maintain"],
                ),
            ):
                path = STATE / (name + ".log")
                write_private(path, "")
                log = path.open("a")
                logs.append(log)
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
            async with asyncio.timeout(60):
                while not stop.is_set():
                    if any(process.poll() is not None for process in processes):
                        raise RpcError("local_process_exited")
                    try:
                        await doctor(client, state)
                        break
                    except RpcError:
                        await asyncio.sleep(0.5)
                else:
                    return
            print(
                f"Local gateway ready: {GATEWAY}; model {state.model}. Ctrl-C stops this demo's worker, Edge and maintenance processes.",
                flush=True,
            )
            while not stop.is_set():
                if any(process.poll() is not None for process in processes):
                    raise RpcError("local_process_exited")
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
        finally:
            # Stop admission before the worker's bounded drain.
            for process in reversed(processes):
                await stop_process(process)
            for log in logs:
                log.close()
            for signum in signals:
                loop.remove_signal_handler(signum)


async def run(args):
    private_directory(STATE)
    async with httpx.AsyncClient(timeout=10) as client:
        if args.action == "setup":
            # The Edge lock also serializes setup/session changes against a
            # running demo or disposable Edge integration harness.
            async with runtime_lock():
                await setup(
                    client,
                    connection(),
                    model=args.model,
                    recover_enrollment=args.recover_enrollment,
                )
            print(
                "Local demo configured; credentials remain in private .oshepherd/local/demo/ files. Run: python -m scripts.supabase_demo serve"
            )
            return
        state = DemoState.load(STATE)
        if args.action == "serve":
            await serve(client, state)
        elif args.action == "maintain":
            from oshepherd.common.logging_config import configure_logging

            configure_logging("maintenance")
            local = connection()
            await run_maintenance(
                SupabaseMaintenanceConfig(
                    SUPABASE_URL=ORIGIN,
                    SUPABASE_SECRET_KEY=local["SECRET_KEY"],
                    SUPABASE_SCHEMA="oshepherd_spike_api",
                    OSHEPHERD_CLUSTER_ID=state.cluster_id,
                )
            )
        elif args.action == "doctor":
            print(json.dumps(await doctor(client, state)))
        else:
            # Reading the client key from its file avoids shell history/process
            # arguments. Real inference is explicit and output is bounded.
            await doctor(client, state)
            request = {
                "model": state.model,
                "prompt": args.prompt,
                "stream": args.stream,
                "options": {"num_predict": 8, "num_ctx": 512, "temperature": 0},
            }
            headers = {
                "Authorization": "Bearer " + state.application_key.get_secret_value()
            }
            if args.stream:
                pending = bytearray()
                total = 0
                done = False
                async with asyncio.timeout(140):
                    async with client.stream(
                        "POST",
                        GATEWAY + "/api/generate",
                        headers=headers,
                        json=request,
                        timeout=140,
                    ) as response:
                        if response.is_error:
                            raise RpcError("local_service_error", response.status_code)
                        async for fragment in response.aiter_bytes():
                            total += len(fragment)
                            if total > RESULT_BYTES:
                                raise RpcError("result_too_large", 413)
                            pending.extend(fragment)
                            while b"\n" in pending:
                                line, _, tail = pending.partition(b"\n")
                                if len(line) + 1 > RECORD_BYTES or done:
                                    raise RpcError("invalid_stream_record")
                                value = json.loads(line)
                                if not isinstance(value, dict) or "error" in value:
                                    raise RpcError("upstream_error")
                                done = value.get("done") is True
                                print(line.decode(), flush=True)
                                pending = bytearray(tail)
                            if len(pending) > RECORD_BYTES:
                                raise RpcError("invalid_stream_record")
                if pending or not done:
                    raise RpcError("incomplete_upstream")
                return
            value = await bounded_json(
                client,
                "POST",
                GATEWAY + "/api/generate",
                headers=headers,
                json=request,
                timeout=140,
            )
            if value.get("done") is not True:
                raise RpcError("incomplete_upstream")
            print(json.dumps(value, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("setup", "serve", "doctor", "request", "maintain")
    )
    parser.add_argument(
        "--model", help="Already installed generation model; required for first setup"
    )
    parser.add_argument(
        "--recover-enrollment",
        action="store_true",
        help="Issue a new one-time code after an ambiguous exchange",
    )
    parser.add_argument("--prompt", default="Reply with the word ready.")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Print exact NDJSON for the bounded inference request",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except RpcError as error:
        if error.code in ("enrollment_exchange_ambiguous", "worker_auth_ambiguous"):
            raise SystemExit(
                "Enrollment outcome is ambiguous; rerun setup --recover-enrollment to issue a new code"
            ) from None
        raise SystemExit(error.code) from None
    except (OSError, ValueError, KeyError, TimeoutError):
        raise SystemExit(
            "Local demo failed; check local prerequisites and private .oshepherd/local/demo/ logs/state"
        ) from None


if __name__ == "__main__":
    main()
