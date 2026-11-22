"""CLI lifecycle composition. Imports no Redis or Celery modules."""

import asyncio
import signal

from .client import RpcClient
from .sessions import WorkerAuth, WorkerSession
from .worker import SupabaseWorker
from .storage import WorkerResultStorage
from .private_files import read_private_json
from .worker_logging import worker_logging
from oshepherd.worker.executor import OllamaExecutor


async def start_worker(config, *, stop=None, ready=None):
    with worker_logging(config.WORKER_SESSION_FILE, config.LOGLEVEL):
        await _run_worker(config, stop=stop, ready=ready)


async def _run_worker(config, *, stop=None, ready=None):
    key = config.SUPABASE_PUBLISHABLE_KEY.get_secret_value()
    session = WorkerSession.load(
        config.WORKER_SESSION_FILE, supabase_url=config.SUPABASE_URL
    )
    rpc = RpcClient(
        config.SUPABASE_URL,
        key,
        access_token=session.access_token.get_secret_value(),
        schema=config.SUPABASE_SCHEMA,
    )
    auth = WorkerAuth(config.SUPABASE_URL, key)
    executor = OllamaExecutor(config.ollama_url)
    owns_signals = stop is None
    stop = stop if stop is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = []
    try:
        if owns_signals:
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, stop.set)
                signals.append(signum)
        # Refresh before registration when starting from an old saved session.
        import time

        if session.expires_at <= time.time() + 60:
            session = await auth.refresh(session)
            session.save(config.WORKER_SESSION_FILE)
            rpc.access_token = session.access_token.get_secret_value()
        worker = SupabaseWorker(
            rpc,
            executor,
            concurrency=config.CONCURRENCY,
            heartbeat_seconds=config.HEARTBEAT_SECONDS,
            poll_seconds=min(1, config.HEARTBEAT_SECONDS),
            drain_seconds=config.DRAIN_SECONDS,
            session=session,
            auth=auth,
            session_file=config.WORKER_SESSION_FILE,
            systemone=config.SYSTEMONE_ENABLED,
            result_storage=WorkerResultStorage(rpc),
        )
        if ready is not None:
            worker.ready = ready
        await worker.run(stop)
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
        await asyncio.gather(rpc.close(), auth.close(), executor.close())


def read_enrollment(filename, *, supabase_url):
    value = read_private_json(filename, limit=16384)
    if (
        not isinstance(value, dict)
        or set(value) != {"v", "supabase_url", "token_hash"}
        or type(value["v"]) is not int
        or value["v"] != 1
        or value["supabase_url"] != supabase_url
    ):
        raise ValueError("Enrollment file belongs to another project or protocol")
    if (
        not isinstance(value["token_hash"], str)
        or not 1 <= len(value["token_hash"]) <= 1024
    ):
        raise ValueError("Invalid enrollment token")
    return value["token_hash"]


async def login_worker(config, token_hash):
    auth = WorkerAuth(
        config.SUPABASE_URL, config.SUPABASE_PUBLISHABLE_KEY.get_secret_value()
    )
    try:
        session = await auth.enroll(token_hash)
        session.save(config.WORKER_SESSION_FILE)
    finally:
        await auth.close()
