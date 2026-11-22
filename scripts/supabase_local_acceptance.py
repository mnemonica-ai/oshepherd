"""Fresh Docker Supabase -> CLI share/join -> actual Ollama Python inference.

Run from the checkout with uv run --locked python -m scripts.supabase_local_acceptance
--work-dir /absolute/private/new-directory. No hosted APIs or package publication.
The work directory must be new. All logs/credentials remain private there.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from oshepherd.backends.supabase.operator import Installation, OperatorConfig
from oshepherd.backends.supabase.private_files import (
    private_directory,
    read_private_json,
    write_private,
    write_private_json,
)
from oshepherd.backends.supabase.releases import Database, release_assets

ROOT = Path(__file__).resolve().parents[1]
API_PORT, DB_PORT, SECOND_OLLAMA_PORT = 56321, 56322, 11435


def wait_for(check, *, seconds=90):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if check():
            return
        time.sleep(0.3)
    raise RuntimeError("readiness_timeout")


def run(args):
    root = args.work_dir.absolute()
    if root.exists():
        raise ValueError("work_directory_must_be_new")
    for port in (API_PORT, DB_PORT, 56320, 56383, SECOND_OLLAMA_PORT):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    root = private_directory(root)
    assets, manifest = release_assets()
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    env.pop("OSHEPHERD_BACKEND", None)
    processes, logs = [], []
    report = {"passed": False, "hosted": False, "published": False, "checks": []}
    cli = [sys.executable, "-c", "from oshepherd.cli import main; main()"]
    # Child CLI processes deliberately run outside the checkout. They use the
    # current environment's installed package (editable or wheel), never PYTHONPATH.
    env.pop("PYTHONPATH", None)

    def command(name, argv, timeout=180):
        result = subprocess.run(
            argv, cwd=root, env=env, capture_output=True, text=True, timeout=timeout
        )
        write_private(root / (name + ".log"), result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("command_failed_" + name)
        return result.stdout

    def start(name, argv, extra=None):
        path = root / (name + ".log")
        write_private(path, "")
        log = path.open("a")
        logs.append(log)
        process = subprocess.Popen(
            argv,
            cwd=root,
            env={**env, **(extra or {})},
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
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise RuntimeError("process_shutdown_timeout")

    def reachable(url, headers=None):
        try:
            return httpx.get(url, headers=headers, timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def message(label):
        print(label, flush=True)
        report["checks"].append(label)
        write_private_json(root / "report.json", report)

    stack_attempted = False
    try:
        version = command("supabase-version", ["supabase", "--version"]).strip()
        if version != manifest["supabase_cli"]:
            raise ValueError("pinned_supabase_cli_required")
        project = "oshepherd-acceptance-" + uuid4().hex[:12]
        config = (ROOT / "supabase/config.toml").read_text()
        for old, new in (
            ("oshepherd-development", project),
            ("55321", str(API_PORT)),
            ("55322", str(DB_PORT)),
            ("55320", "56320"),
            ("55383", "56383"),
        ):
            config = config.replace(old, new)
        write_private(root / "supabase/config.toml", config)
        shutil.copytree(assets / "functions", root / "supabase/functions")
        # Deliberately no migrations directory: oshepherd install owns bootstrap.
        report["project_id"] = project
        message("Starting a fresh isolated Supabase stack")
        stack_attempted = True
        command(
            "stack-start",
            [
                "supabase",
                "start",
                "--exclude",
                "realtime,studio,logflare,vector,postgres-meta,supavisor,imgproxy,mailpit",
            ],
            timeout=300,
        )
        status = json.loads(
            command("stack-status", ["supabase", "status", "-o", "json"])
        )
        write_private_json(root / "status.json", status)
        operator_dir = root / "operator"
        connection = operator_dir / "connection.json"
        command(
            "configure",
            cli
            + [
                "supabase",
                "configure",
                "--local-from",
                str(root / "status.json"),
                "--output",
                str(connection),
            ],
        )
        config = OperatorConfig.load(connection)
        database = Database(config)
        assert (
            database.execute("select to_regnamespace('oshepherd') is null;") == "t"
        ), "database_not_clean"
        options = [
            "--connection-file",
            str(connection),
            "--state-dir",
            str(operator_dir),
        ]
        command("install", cli + ["supabase", "install", *options, "--create-owner"])
        first = Installation.load(operator_dir, config)
        command(
            "install-repeat", cli + ["supabase", "install", *options, "--create-owner"]
        )
        repeated = Installation.load(operator_dir, config)
        assert (first.cluster_id, first.application_key) == (
            repeated.cluster_id,
            repeated.application_key,
        )
        message(
            "CLI installed a fresh database; repeat installation preserved identity"
        )
        edge = start(
            "edge",
            [
                "supabase",
                "functions",
                "serve",
                "ollama",
                "--env-file",
                str(operator_dir / "edge.env"),
                "--no-verify-jwt",
            ],
        )
        settings = read_private_json(operator_dir / "client.json")
        wait_for(
            lambda: edge.poll() is None
            and reachable(
                settings["gateway_url"] + "/health",
                {"Authorization": "Bearer " + settings["application_key"]},
            )
        )
        if not reachable("http://127.0.0.1:11434/api/tags"):
            start(
                "ollama-primary",
                ["ollama", "serve"],
                {"OLLAMA_HOST": "127.0.0.1:11434"},
            )
            wait_for(lambda: reachable("http://127.0.0.1:11434/api/tags"))
        share_args = cli + [
            "share",
            *options,
            "--model",
            args.model,
            "--ollama-url",
            "http://127.0.0.1:11434",
        ]
        share = start("share", share_args)
        wait_for(
            lambda: share.poll() is None
            and "Inference verified." in (root / "share.log").read_text(),
            seconds=180,
        )
        client_args = [
            sys.executable,
            str(ROOT / "examples/supabase_client.py"),
            "--client-file",
            str(operator_dir / "client.json"),
            "--model",
            args.model,
        ]
        if args.embedding_model:
            client_args += ["--embedding-model", args.embedding_model]
        if args.decision_model:
            client_args += ["--decision-model", args.decision_model]
        report["primary_client"] = json.loads(
            command("primary-client", client_args, timeout=600)
        )
        message("Official Python SDK passed through Edge to the first Ollama server")
        command(
            "invite",
            cli
            + [
                "invite",
                *options,
                "--name",
                "second",
                "--output",
                str(root / "second.invitation.json"),
            ],
        )
        second = start(
            "ollama-second",
            ["ollama", "serve"],
            {"OLLAMA_HOST": f"127.0.0.1:{SECOND_OLLAMA_PORT}"},
        )
        wait_for(
            lambda: second.poll() is None
            and reachable(f"http://127.0.0.1:{SECOND_OLLAMA_PORT}/api/tags")
        )
        joined_dir = root / "joined"
        join_args = cli + [
            "join",
            "--state-dir",
            str(joined_dir),
            "--ollama-url",
            f"http://127.0.0.1:{SECOND_OLLAMA_PORT}",
            "--model",
            args.model,
        ]
        joined = start(
            "join",
            join_args + ["--invitation-file", str(root / "second.invitation.json")],
        )
        state = Installation.load(operator_dir, config)
        worker = str(state.workers["second"].worker_id)

        def generation():
            return int(
                database.execute(
                    f"select generation from oshepherd.workers where id='{worker}';"
                )
            )

        wait_for(lambda: joined.poll() is None and generation() > 0)
        generation_before = generation()
        stop(share)
        # With the original worker stopped, successful jobs must belong to join.
        report["joined_client"] = json.loads(
            command("joined-client", client_args, timeout=600)
        )
        assert (
            int(
                database.execute(
                    f"select count(*) from oshepherd.jobs where worker_id='{worker}' and state='succeeded';"
                )
            )
            >= 6
        )
        for path in joined_dir.glob("*.json"):
            data = path.read_text()
            for secret in (
                config.secret_key.get_secret_value(),
                config.database_url.get_secret_value(),
                state.application_key.get_secret_value(),
            ):
                assert secret not in data, "operator_secret_in_joined_state"
        message(
            "Second Ollama server joined without admin credentials and completed SDK inference"
        )
        stop(joined)
        joined = start("join-restart", join_args)
        wait_for(lambda: joined.poll() is None and generation() > generation_before)
        report["restart_client"] = json.loads(
            command("restart-client", client_args, timeout=600)
        )
        message(
            "Joined worker restarted with its saved session and completed inference"
        )
        report["passed"] = True
        if args.serve:
            share = start("share-resumed", share_args)
            wait_for(
                lambda: share.poll() is None
                and "Inference verified." in (root / "share-resumed.log").read_text(),
                seconds=180,
            )
            message(
                "Ready for manual requests; Ctrl-C stops and removes this test stack"
            )
            print(
                "Client configuration: " + str(operator_dir / "client.json"), flush=True
            )
            try:
                while True:
                    if any(p.poll() is not None for p in (share, joined, edge, second)):
                        raise RuntimeError("manual_service_stopped")
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    finally:
        cleanup_errors = []
        for process in reversed(processes):
            try:
                stop(process)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        for log in logs:
            log.close()
        if stack_attempted:
            try:
                command("stack-stop", ["supabase", "stop", "--no-backup"], timeout=120)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        report["cleanup_passed"] = not cleanup_errors
        if cleanup_errors or sys.exc_info()[0] is not None:
            report["passed"] = False
        write_private_json(root / "report.json", report)
        if cleanup_errors:
            raise RuntimeError("owned_resource_cleanup_failed_see_private_logs")
    print("Acceptance passed. Private report: " + str(root / "report.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--embedding-model", default="embeddinggemma:latest")
    parser.add_argument(
        "--decision-model",
        help="Optional installed compatible System One model; never downloaded automatically",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="After acceptance, keep both workers and Edge running until Ctrl-C",
    )
    run(parser.parse_args())
