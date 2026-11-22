"""Real local Edge runtime with protected logs and scoped process cleanup."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import signal
import subprocess
import tempfile

import httpx
import uvicorn

from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.backends.supabase.storage import WorkerResultStorage
from oshepherd.worker.executor import OllamaExecutor
from scripts.supabase_local import private_write
from scripts.local_runtime import runtime_lock
from .harness import queue_harness

ROOT = Path(__file__).resolve().parents[3]


@asynccontextmanager
async def edge_runtime(h):
    async with runtime_lock():
        async with serve_edge(h) as url:
            yield url


@asynccontextmanager
async def serve_edge(h):
    state = ROOT / ".oshepherd/local"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="edge-test-", dir=state) as directory:
        path = Path(directory)
        env_file = path / "edge.env"
        private_write(
            env_file,
            f"OSHEPHERD_CLUSTER_ID={h.cluster}\nOSHEPHERD_SCHEMA={h.admin.schema}\nOSHEPHERD_PUBLIC_URL={h.connection['API_URL']}\n",
        )
        descriptor = os.open(
            path / "runtime.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        env = dict(os.environ)
        env.pop("SUPABASE_ACCESS_TOKEN", None)
        with os.fdopen(descriptor, "w") as log:
            process = subprocess.Popen(
                [
                    "supabase",
                    "functions",
                    "serve",
                    "ollama",
                    "--env-file",
                    str(env_file),
                    "--no-verify-jwt",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                async with asyncio.timeout(60):
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError(
                                "Local Edge process exited; inspect protected runtime log"
                            )
                        try:
                            response = await h.client.get(
                                h.connection["API_URL"] + "/functions/v1/ollama/health",
                                headers={"Authorization": "Bearer " + h.keys[0]},
                                timeout=2,
                            )
                            if response.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.2)
                yield h.connection["API_URL"] + "/functions/v1/ollama"
            except BaseException:
                # Preserve redacted runtime diagnostics for failed experiments.
                private_write(
                    state / "edge-last-failure.log", (path / "runtime.log").read_text()
                )
                raise
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.to_thread(process.wait, 5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        await asyncio.to_thread(process.wait)


@asynccontextmanager
async def edge_lifecycle(
    connection,
    *,
    app=None,
    ollama_url=None,
    rpc_schema="oshepherd_spike_api",
    **worker_options,
):
    async with queue_harness(connection, workers=1, schema=rpc_schema) as h:
        server, server_task = None, None
        if app is not None:
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=55324,
                    log_level="critical",
                    lifespan="off",
                    access_log=False,
                )
            )
            server_task = asyncio.create_task(server.serve())
            async with asyncio.timeout(5):
                while not server.started:
                    if server_task.done():
                        await server_task
                        raise RuntimeError("Fake upstream did not start")
                    await asyncio.sleep(0.02)
            ollama_url = "http://127.0.0.1:55324"
        executor = OllamaExecutor(ollama_url)
        worker = SupabaseWorker(
            h.workers[0]["rpc"],
            executor,
            heartbeat_seconds=0.2,
            poll_seconds=0.1,
            drain_seconds=0,
            result_storage=WorkerResultStorage(h.workers[0]["rpc"]),
            **worker_options,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        try:
            async with asyncio.timeout(30):
                while not worker.ready.is_set():
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            async with edge_runtime(h) as url:
                yield h, url, worker, task
        finally:
            stop.set()
            await asyncio.wait_for(task, 10)
            await executor.close()
            if server is not None:
                server.should_exit = True
                await asyncio.wait_for(server_task, 5)
