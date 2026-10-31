"""Exercise installed share/invite/join against the isolated local Supabase stack.

This validates the existing-installation slice, not hosted project creation.
Run this absolute script with uv --isolated --no-project --with <candidate wheel>.
Requires running local Supabase and Ollama with an already installed model.
Creates a new cluster, starts only owned processes, then disables the cluster.
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
from oshepherd.backends.supabase.private_files import (
    private_directory,
    read_private_json,
    write_private,
    write_private_json,
)
from oshepherd.backends.supabase.releases import Database, release_assets


async def run(args):
    assert "site-packages" in Path(oshepherd.__file__).parts, "installed_wheel_required"
    release_assets()
    local = read_private_json(args.local_from)
    assert local["API_URL"] == "http://127.0.0.1:55321", "isolated_stack_required"
    root = private_directory(args.work_dir)
    assert not any(root.iterdir()), "fresh_work_directory_required"
    operator_dir = root / "operator"
    connection = root / "connection.json"
    config = OperatorConfig(
        project_ref="local",
        supabase_url=local["API_URL"],
        publishable_key=local["PUBLISHABLE_KEY"],
        secret_key=local["SECRET_KEY"],
        database_url="postgresql://supabase_admin:postgres@127.0.0.1:55322/postgres",
        gateway_url=f"http://127.0.0.1:{args.port}",
    )
    data = config.model_dump(mode="json")
    for field in ("publishable_key", "secret_key", "database_url"):
        data[field] = getattr(config, field).get_secret_value()
    write_private_json(connection, data)
    operator = Operator(config, operator_dir)
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
    owned_service = None
    logs = []
    result = {
        "installed_wheel": True,
        "existing_local_installation_only": True,
        "hosted_qualified": False,
        "checks": [],
        "passed": False,
    }

    def cli(name, *arguments):
        completed = subprocess.run(
            command + list(arguments),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=240,
        )
        write_private(root / (name + ".log"), completed.stdout + completed.stderr)
        assert completed.returncode == 0, "onboarding_cli_failed_" + name
        result["checks"].append(name)
        return completed.stdout

    def start(name, *arguments):
        path = root / (name + ".log")
        write_private(path, "")
        log = path.open("a")
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
        for signum, timeout in (
            (signal.SIGINT, 40),
            (signal.SIGTERM, 15),
            (signal.SIGKILL, 5),
        ):
            if process.poll() is not None:
                return
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                process.wait(timeout=5)
                return
            try:
                process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                continue
        raise RuntimeError("owned_onboarding_process_cleanup_timeout")

    async def generation(worker_name):
        state = Installation.load(operator_dir, config)
        worker_id = state.workers[worker_name].worker_id
        # UUID-validated owned identifiers only, never secret/user SQL text.
        query = (
            "select generation from oshepherd.workers where id='"
            + str(UUID(str(worker_id)))
            + "' and cluster_id='"
            + str(UUID(str(state.cluster_id)))
            + "';"
        )
        return int(await asyncio.to_thread(Database(config).execute, query))

    async def wait_registered(process, worker_name, previous=0):
        async with asyncio.timeout(60):
            while True:
                assert process.poll() is None, "onboarding_worker_stopped"
                if await generation(worker_name) > previous:
                    return
                await asyncio.sleep(0.5)

    try:
        # Prepare an existing installation/gateway, the input to A2. Provider
        # creation is deliberately excluded from this local acceptance claim.
        owner = await operator.auth_request(
            "POST",
            "users",
            json={
                "email": f"onboarding-check-{uuid4()}@enrollment.invalid",
                "email_confirm": True,
            },
        )
        await operator.install(UUID(owner["id"]))
        state = Installation.load(operator_dir, config)
        identity = (state.cluster_id, state.key_id, state.application_key)
        gateway_env = root / "gateway.env"
        write_private(
            gateway_env,
            (operator_dir / "gateway.env").read_text()
            + f'HOST="127.0.0.1"\nPORT="{args.port}"\n',
        )
        gateway = start(
            "gateway",
            "start-api",
            "--backend",
            "supabase",
            "--env-file",
            str(gateway_env),
        )
        async with asyncio.timeout(30):
            while not (await operator.doctor())["gateway_available"]:
                assert gateway.poll() is None, "onboarding_gateway_stopped"
                await asyncio.sleep(0.5)
        common = [
            "--connection-file",
            str(connection),
            "--state-dir",
            str(operator_dir),
        ]
        for name in ("share", "share-resume"):
            await asyncio.to_thread(
                cli,
                name,
                "share",
                *common,
                "--model",
                args.model,
                "--check",
            )
            current = Installation.load(operator_dir, config)
            assert (
                current.cluster_id,
                current.key_id,
                current.application_key,
            ) == identity
            current_generation = await generation("local")
            if name == "share":
                first_generation = current_generation
            else:
                assert (
                    current_generation > first_generation
                ), "share_did_not_resume_worker"
        result["share_restart_generation_advanced"] = True
        foreground = start("share-foreground", "share", *common, "--model", args.model)
        async with asyncio.timeout(180):
            while (
                "Sharing in the foreground."
                not in (root / "share-foreground.log").read_text()
            ):
                assert foreground.poll() is None, "foreground_share_stopped"
                await asyncio.sleep(0.5)
        await asyncio.to_thread(stop, foreground)
        assert foreground.returncode == 0, "foreground_shutdown_failed"
        assert (
            "Worker and cleanup stopped." in (root / "share-foreground.log").read_text()
        )
        result["foreground_share_sigint_shutdown"] = True
        invitation = root / "another-machine.invitation.json"
        await asyncio.to_thread(
            cli,
            "invite",
            "invite",
            *common,
            "--name",
            "another-machine",
            "--output",
            str(invitation),
        )
        join_dir = root / "joined-machine"
        worker = start(
            "join",
            "join",
            "--invitation-file",
            str(invitation),
            "--state-dir",
            str(join_dir),
            "--model",
            args.model,
        )
        await wait_registered(worker, "another-machine")
        previous_generation = await generation("another-machine")
        # The joined machine has no administrative or client application secret.
        for path in join_dir.rglob("*"):
            if path.is_file():
                contents = path.read_bytes()
                assert config.secret_key.get_secret_value().encode() not in contents
                assert state.application_key.get_secret_value().encode() not in contents
                assert config.database_url.get_secret_value().encode() not in contents
        result["joined_machine_credential_isolation"] = True
        await asyncio.to_thread(stop, worker)
        assert worker.returncode == 0, "joined_shutdown_failed"
        worker = start(
            "join-resume",
            "join",
            "--state-dir",
            str(join_dir),
            "--model",
            args.model,
        )
        await wait_registered(worker, "another-machine", previous_generation)
        result["join_restart_generation_advanced"] = True
        # Only the joined worker is alive; verify a real completion and stream.
        async with asyncio.timeout(60):
            while not (await operator.doctor())["models_available"]:
                assert worker.poll() is None, "joined_worker_stopped"
                await asyncio.sleep(0.5)
        await operator.verify_inference(args.model)
        result["joined_real_json"] = True
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                config.gateway_url + "/api/generate",
                headers={
                    "Authorization": "Bearer "
                    + state.application_key.get_secret_value()
                },
                json={
                    "model": args.model,
                    "prompt": "Say hello",
                    "stream": True,
                    "options": {"num_predict": 8, "num_ctx": 512},
                },
            )
        assert response.status_code == 200, "joined_stream_failed"
        assert json.loads(response.content.splitlines()[-1])["done"] is True
        result["joined_real_terminal_ndjson"] = True
        if args.service_check:
            from oshepherd.backends.supabase.service import UserService

            await asyncio.to_thread(stop, worker)
            assert worker.returncode == 0, "joined_shutdown_before_service_failed"
            owned_service = UserService(args.service_name, root=args.service_root)
            assert (
                not owned_service.receipt.exists() and not owned_service.unit.exists()
            ), "acceptance_service_already_exists"

            async def service_ready(phase, worker_name, previous):
                async with asyncio.timeout(210):
                    while True:
                        status = await asyncio.to_thread(owned_service.status)
                        if status["phase"] == "failed":
                            raise AssertionError("acceptance_service_runtime_failed")
                        if (
                            status["manager_active"]
                            and status["phase"] == phase
                            and await generation(worker_name) > previous
                        ):
                            return status
                        await asyncio.sleep(0.5)

            async def service_inference():
                await operator.verify_inference(args.model)
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post(
                        config.gateway_url + "/api/generate",
                        headers={
                            "Authorization": "Bearer "
                            + state.application_key.get_secret_value()
                        },
                        json={
                            "model": args.model,
                            "prompt": "Say hello",
                            "stream": True,
                            "options": {"num_predict": 8, "num_ctx": 512},
                        },
                    )
                assert response.status_code == 200, "service_stream_failed"
                assert (
                    json.loads(response.content.splitlines()[-1])["done"] is True
                ), "service_stream_incomplete"

            # Both roles use the same service name sequentially; uninstall proves
            # the owned launch registration is removed before the next install.
            for kind, directory, worker_name, phase in (
                ("share", operator_dir, "local", "sharing_foreground"),
                ("join", join_dir, "another-machine", "worker_connected"),
            ):
                previous = await generation(worker_name)
                await asyncio.to_thread(
                    owned_service.install,
                    kind,
                    directory,
                    connection_file=connection if kind == "share" else None,
                )
                await service_ready(phase, worker_name, previous)
                await service_inference()
                result["service_" + kind + "_real_json_ndjson"] = True
                previous = await generation(worker_name)
                await asyncio.to_thread(owned_service.stop)
                assert not (await asyncio.to_thread(owned_service.status))[
                    "manager_active"
                ], "service_stop_failed"
                await asyncio.to_thread(owned_service.start)
                await service_ready(phase, worker_name, previous)
                await service_inference()
                result["service_" + kind + "_restart_generation_advanced"] = True
                await asyncio.to_thread(owned_service.uninstall)
                assert (
                    not owned_service.unit.exists()
                    and not owned_service.receipt.exists()
                ), "service_uninstall_failed"
                assert (
                    directory
                    / (
                        "local.worker-session.json"
                        if kind == "share"
                        else "worker-session.json"
                    )
                ).exists(), "service_uninstall_removed_worker_state"
            result["service_cleanup_preserved_worker_state"] = True
            result["service_platform"] = owned_service.system
        result["passed"] = True
    finally:
        cleanup_errors = []
        if owned_service is not None and owned_service.receipt.exists():
            try:
                receipt = owned_service.owned()
                assert receipt["target"]["state_dir"] in {
                    str(operator_dir),
                    str(root / "joined-machine"),
                }, "service_cleanup_target_mismatch"
                await asyncio.to_thread(owned_service.uninstall)
            except Exception:
                cleanup_errors.append("service")
        for process in reversed(processes):
            try:
                await asyncio.to_thread(stop, process)
            except Exception:
                cleanup_errors.append("process")
        for log in logs:
            log.close()
        try:
            if (operator_dir / "installation.json").exists():
                await operator.disable()
        except Exception:
            cleanup_errors.append("cluster")
        finally:
            await operator.close()
            if cleanup_errors:
                result["passed"] = False
                result["cleanup_failed"] = cleanup_errors
            write_private_json(args.report_file, result)
        if cleanup_errors:
            raise RuntimeError("onboarding_acceptance_cleanup_failed")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-from", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-file", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=55326)
    parser.add_argument(
        "--service-check",
        action="store_true",
        help="Explicitly install, exercise and remove isolated real per-user services.",
    )
    parser.add_argument("--service-name", default="adoption-20261031")
    parser.add_argument(
        "--service-root",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / ".oshepherd"
        / "local"
        / "services",
    )
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    asyncio.run(run(arguments))
