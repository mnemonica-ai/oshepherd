"""Report cancellation capability counters; keep proxy source private."""

import asyncio
import json
from pathlib import Path
import subprocess
import shlex
import re
import hashlib

from scripts.supabase_local import private_write

from tests.integration.supabase.edge_harness import edge_lifecycle
from tests.integration.supabase.test_worker import upstream


async def main():
    data = json.loads(Path(".oshepherd/local/connection.json").read_text())
    async with edge_lifecycle(data, app=upstream()):
        command = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Config.Cmd}}",
                "supabase_edge_runtime_oshepherd-development",
            ],
            capture_output=True,
            text=True,
        )
        if command.returncode:
            raise SystemExit("Isolated Edge container not found")
        arguments = json.loads(command.stdout)
        arguments = [part for argument in arguments for part in shlex.split(argument)]
        entry = next(
            (
                part.split("=", 1)[1]
                for part in arguments
                if part.startswith("--main-service=")
            ),
            None,
        )
        if entry is None and "--main-service" in arguments:
            entry = arguments[arguments.index("--main-service") + 1]
        if entry is None:
            raise SystemExit("Local proxy entry flag unavailable")
        if not entry.startswith("/"):
            raise SystemExit("Unexpected local proxy entry point")
        result = subprocess.run(
            [
                "docker",
                "exec",
                "supabase_edge_runtime_oshepherd-development",
                "cat",
                entry.rstrip("/") + "/index.ts",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise SystemExit("Could not inspect the isolated Edge proxy source")
        private_write(Path(".oshepherd/local/edge-proxy-source.ts"), result.stdout)
        print("Proxy source bytes:", len(result.stdout.encode()))
        print("Proxy SHA256:", hashlib.sha256(result.stdout.encode()).hexdigest())
        print(
            "Proxy flags:",
            ", ".join(
                part.split("=")[0] for part in arguments if part.startswith("--")
            ),
        )
        for term in ("fetch", "signal", "AbortController", "completed", "abort"):
            matches = list(re.finditer(term, result.stdout))
            print(f"Proxy {term} occurrences: {len(matches)}")
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Config.Image}}",
                "supabase_edge_runtime_oshepherd-development",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("Image:", result.stdout.strip())


if __name__ == "__main__":
    asyncio.run(main())
