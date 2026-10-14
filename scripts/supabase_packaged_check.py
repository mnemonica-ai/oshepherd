"""Fresh local CLI lifecycle from an installed wheel, outside the checkout.

Run this absolute script path with uv --isolated --no-project --with <wheel>.
Requires the existing isolated local stack and an already installed Ollama model.
Uses a new owned cluster/identity, no Redis and no provider-resource deletion.
The final disabled cluster and private journals remain for recovery inspection.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import oshepherd

from oshepherd.backends.supabase.operator import Installation, Operator, OperatorConfig
from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.private_files import (
    private_directory,
    write_private,
    write_private_json,
)
from oshepherd.backends.supabase.releases import Database, release_assets


async def run(args):
    assert "site-packages" in Path(oshepherd.__file__).parts, "installed_wheel_required"
    release_assets()
    root = private_directory(args.work_dir)
    assert not (root / "connection.json").exists(), "fresh_work_directory_required"
    command = [
        "uv",
        "run",
        "--no-project",
        "--no-sync",
        "--python",
        sys.executable,
        "python",
        "-m",
        "oshepherd.cli",
    ]
    processes = []
    logs = []
    results = {"installed_wheel": True, "hosted_qualified": False, "checks": []}

    def cli(*arguments, expected_success=True):
        process = subprocess.run(
            command + list(arguments),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        write_private(root / "last-cli.log", process.stdout + process.stderr)
        assert (process.returncode == 0) == expected_success, (
            "packaged_cli_failed_" + arguments[0]
        )
        results["checks"].append(" ".join(arguments[:2]))
        if expected_success:
            try:
                return json.loads(process.stdout)
            except ValueError:
                return None

    def start(name, *arguments):
        log = (root / (name + ".log")).open("w")
        os.chmod(log.name, 0o600)
        logs.append(log)
        process = subprocess.Popen(
            command + list(arguments),
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)

    connection = root / "connection.json"
    state_dir = root / "operator"
    options = ["--connection-file", str(connection), "--state-dir", str(state_dir)]
    operator = None
    try:
        await asyncio.to_thread(
            cli,
            "supabase",
            "configure",
            "--local-from",
            str(args.local_from),
            "--output",
            str(connection),
        )
        config = OperatorConfig.load(connection)
        data = json.loads(connection.read_text())
        data["gateway_url"] = "http://127.0.0.1:" + str(args.port)
        write_private_json(connection, data)
        config = OperatorConfig.load(connection)
        operator = Operator(config, state_dir)
        owner = await operator.auth_request(
            "POST",
            "users",
            json={
                "email": "packaged-check-" + str(uuid4()) + "@enrollment.invalid",
                "email_confirm": True,
            },
        )
        owner_id = str(UUID(owner["id"]))
        await asyncio.to_thread(
            cli, "supabase", "install", *options, "--owner-subject", owner_id
        )
        state = Installation.load(state_dir, config)
        identities = (state.cluster_id, state.key_id, state.application_key)
        for operation in ("install", "upgrade"):
            extra = ["--owner-subject", owner_id] if operation == "install" else []
            await asyncio.to_thread(cli, "supabase", operation, *options, *extra)
            current = Installation.load(state_dir, config)
            assert (
                current.cluster_id,
                current.key_id,
                current.application_key,
            ) == identities
        await asyncio.to_thread(
            cli,
            "supabase",
            "enroll-worker",
            *options,
            "--name",
            "local",
            "--capacity",
            "1",
        )
        before = Installation.load(state_dir, config).workers["local"]
        await asyncio.to_thread(
            cli,
            "supabase",
            "enroll-worker",
            *options,
            "--name",
            "local",
            "--capacity",
            "1",
        )
        assert Installation.load(state_dir, config).workers["local"] == before
        worker_env = state_dir / "local.worker.env"
        write_private(
            worker_env,
            worker_env.read_text() + 'HEARTBEAT_SECONDS="1"\nDRAIN_SECONDS="0"\n',
        )
        assert config.secret_key.get_secret_value() not in worker_env.read_text()
        await asyncio.to_thread(
            cli,
            "supabase-worker-login",
            "--env-file",
            str(worker_env),
            "--enrollment-file",
            str(state_dir / "local.enrollment.json"),
        )
        gateway_env = state_dir / "gateway.env"
        write_private(
            gateway_env,
            gateway_env.read_text() + f'HOST="127.0.0.1"\nPORT="{args.port}"\n',
        )
        gateway = start(
            "gateway",
            "start-api",
            "--backend",
            "supabase",
            "--env-file",
            str(gateway_env),
        )
        worker = start(
            "worker",
            "start-worker",
            "--backend",
            "supabase",
            "--env-file",
            str(worker_env),
        )
        async with httpx.AsyncClient(timeout=30) as client:
            async with asyncio.timeout(60):
                while True:
                    assert (
                        gateway.poll() is None and worker.poll() is None
                    ), "packaged_process_stopped"
                    readiness = await operator.doctor()
                    if readiness["gateway_available"] and readiness["models_available"]:
                        break
                    await asyncio.sleep(1)
            await asyncio.to_thread(cli, "supabase", "doctor", *options)
            await asyncio.to_thread(
                cli, "supabase", "verify-inference", *options, "--model", args.model
            )
            headers = {
                "Authorization": "Bearer " + state.application_key.get_secret_value()
            }
            response = await client.post(
                config.gateway_url + "/api/generate",
                headers=headers,
                json={
                    "model": args.model,
                    "prompt": "Say hello",
                    "stream": True,
                    "options": {"num_predict": 8},
                },
                timeout=90,
            )
            assert (
                response.status_code == 200
                and json.loads(response.content.splitlines()[-1])["done"] is True
            )
            results["real_terminal_ndjson"] = True
            database = Database(config)
            generation_query = (
                "select generation from oshepherd.workers where id='"
                + str(UUID(str(before.worker_id)))
                + "' and cluster_id='"
                + str(UUID(str(state.cluster_id)))
                + "';"
            )
            before_restart = int(
                await asyncio.to_thread(database.execute, generation_query)
            )
            await asyncio.to_thread(stop, worker)
            worker = start(
                "worker-restart",
                "start-worker",
                "--backend",
                "supabase",
                "--env-file",
                str(worker_env),
            )
            async with asyncio.timeout(30):
                while True:
                    assert worker.poll() is None, "packaged_restart_failed"
                    query = await operator.rpc.rpc(
                        "installation_info", {"cluster_id": str(state.cluster_id)}
                    )
                    current_generation = int(
                        await asyncio.to_thread(database.execute, generation_query)
                    )
                    if (
                        query.get("online_workers", 0)
                        and current_generation > before_restart
                    ):
                        break
                    await asyncio.sleep(1)
            results["worker_restart"] = True
            results["generation_advanced"] = True
            await asyncio.to_thread(cli, "supabase", "rotate-key", *options)
            assert (
                await client.get(config.gateway_url + "/health", headers=headers)
            ).status_code == 401
            await asyncio.to_thread(
                cli, "supabase", "revoke-worker", *options, "--name", "local"
            )
        await asyncio.to_thread(cli, "supabase", "uninstall", *options)
        await asyncio.to_thread(cli, "supabase", "uninstall", *options)
        assert not (await operator.doctor())["enabled"]
        results["packaged_lifecycle_passed"] = True
    finally:
        try:
            for process in reversed(processes):
                await asyncio.to_thread(stop, process)
            for log in logs:
                log.close()
            if operator and (state_dir / "installation.json").exists():
                try:
                    await operator.disable()
                except RpcError as error:
                    # Install can fail before its saved cluster UUID exists in
                    # SQL. That has no cluster to disable; retain the real error.
                    if error.code != "not_found":
                        results["packaged_lifecycle_passed"] = False
                        raise
        finally:
            if operator:
                await operator.close()
            write_private_json(args.report_file, results)
    print(json.dumps(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-from", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-file", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=55326)
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    asyncio.run(run(arguments))
