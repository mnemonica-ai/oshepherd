"""One Edge CLI process at a time for this checkout, including tests and demos."""

import asyncio
from contextlib import asynccontextmanager
import fcntl
import os
from pathlib import Path
import signal
import subprocess

from oshepherd.backends.supabase.private_files import private_directory

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def runtime_lock():
    state = private_directory(ROOT / ".oshepherd/local")
    descriptor = os.open(
        state / "edge-runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        async with asyncio.timeout(60):
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.1)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


async def stop_process(process, *, timeout=25):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.to_thread(process.wait, timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.wait)
    except ProcessLookupError:
        pass
