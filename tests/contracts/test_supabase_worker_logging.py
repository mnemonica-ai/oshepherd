"""Lifecycle evidence must reflect RPC acknowledgements without leaking content."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import ollama
import pytest

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.backends.supabase.worker_logging import (
    LOGGER_NAME,
    PrivateRotatingFileHandler,
    worker_logging,
)


@pytest.fixture
def job():
    now = datetime.now(timezone.utc)
    return {
        "job_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "execution_deadline": (now + timedelta(seconds=30)).isoformat(),
        "max_output_bytes": 10000,
        "request": {
            "type": "generate",
            "payload": {"model": "llama3.2:3b", "prompt": "private-prompt"},
        },
    }, now.isoformat()


def worker(executor=None, rpc=None):
    instance = SupabaseWorker(
        rpc or SimpleNamespace(rpc=AsyncMock(return_value={})),
        executor
        or SimpleNamespace(
            complete=AsyncMock(return_value={"response": "private-response"})
        ),
    )
    instance.session = {"session_id": str(uuid4()), "generation": 1}
    instance.worker_id = str(uuid4())
    return instance


def events(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == LOGGER_NAME
    ]


@pytest.mark.parametrize("stream", [False, True])
def test_success_is_logged_after_finish_without_content(caplog, job, stream):
    claim, now = job
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    instance = worker()
    claim["request"]["payload"]["stream"] = stream

    async def records(*args):
        yield {"response": "private-response", "done": False}
        yield {"response": "private-response-final", "done": True}

    instance.executor.iter_records = records

    async def rpc(name, request):
        assert not any(e["event"] == "job_succeeded" for e in events(caplog))
        return {}

    instance.rpc.rpc.side_effect = rpc
    asyncio.run(instance.execute(claim, now))
    logged = events(caplog)
    assert [e["event"] for e in logged] == ["job_started", "job_succeeded"]
    assert logged[-1]["job_id"] == claim["job_id"]
    assert logged[-1]["attempt_id"] == claim["attempt_id"]
    assert logged[-1]["worker_id"] == instance.worker_id
    assert logged[-1]["model"] == "llama3.2:3b"
    assert logged[-1]["duration_ms"] >= 0
    assert "private-" not in caplog.text


@pytest.mark.parametrize(
    "code,event",
    [
        ("backend_unavailable", "attempt_unconfirmed"),
        ("stale_attempt", "attempt_abandoned"),
        ("worker_revoked", "attempt_abandoned"),
    ],
)
def test_finish_error_never_reports_success(caplog, job, code, event):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    instance = worker()
    instance.rpc.rpc.side_effect = RpcError(code)
    if event == "attempt_unconfirmed":
        with pytest.raises(RpcError):
            asyncio.run(instance.execute(*job))
    else:
        asyncio.run(instance.execute(*job))
    assert events(caplog)[-1]["event"] == event
    assert events(caplog)[-1]["error_code"] == code
    assert "job_succeeded" not in caplog.text


@pytest.mark.parametrize(
    "ack_error,expected",
    [
        (None, "attempt_failed"),
        ("stale_attempt", "attempt_abandoned"),
        ("backend_unavailable", "attempt_unconfirmed"),
    ],
)
def test_failure_receipt_and_exception_redaction(caplog, job, ack_error, expected):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    instance = worker(
        SimpleNamespace(
            complete=AsyncMock(
                side_effect=ollama.ResponseError("private-upstream-body", 500)
            )
        )
    )
    if ack_error:
        instance.rpc.rpc.side_effect = RpcError(ack_error)
    if expected == "attempt_unconfirmed":
        with pytest.raises(RpcError):
            asyncio.run(instance.execute(*job))
    else:
        asyncio.run(instance.execute(*job))
    assert events(caplog)[-1]["event"] == expected
    assert "private-" not in caplog.text
    assert instance.rpc.rpc.call_args.args[0] == "fail_attempt"


def test_cancel_logs_local_cancellation_not_success(caplog, job):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    instance = worker()

    async def run():
        started = asyncio.Event()

        async def complete(*args):
            started.set()
            await asyncio.Event().wait()

        instance.executor.complete = complete
        task = asyncio.create_task(instance.execute(*job))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert events(caplog)[-1]["event"] == "attempt_cancelled"
    instance.rpc.rpc.assert_not_called()


def test_claim_replay_is_not_logged_twice(caplog, job):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    claim, now = job
    instance = worker()
    instance.rpc.rpc.return_value = {"stop": [], "jobs": [claim], "server_now": now}

    async def run():
        await instance.control_once()
        await instance.control_once()
        await asyncio.gather(*(task for _, task in instance.jobs.values()))

    asyncio.run(run())
    assert [e["event"] for e in events(caplog)] == [
        "job_claimed",
        "job_started",
        "job_succeeded",
    ]


def test_expired_execution_and_unknown_exception(caplog, job):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    instance = worker(
        SimpleNamespace(complete=AsyncMock(side_effect=TimeoutError("private-timeout")))
    )
    asyncio.run(instance.execute(*job))
    assert events(caplog)[-1]["error_code"] == "execution_expired"
    caplog.clear()
    instance.executor.complete.side_effect = RuntimeError("private-exception")
    with pytest.raises(RuntimeError):
        asyncio.run(instance.execute(*job))
    assert events(caplog)[-1]["event"] == "attempt_unconfirmed"
    assert events(caplog)[-1]["error_code"] == "internal_error"
    assert "private-" not in caplog.text


def test_log_fields_cannot_inject_lines_or_echo_arbitrary_errors(caplog, job):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    claim, _ = job
    claim["job_id"] = "private-token"
    claim["request"]["payload"]["model"] = "model\nprivate-injected-line"
    claim["request"]["type"] = "private-operation"
    worker().log_job("attempt_failed", claim, error_code="private_error_token")
    assert "private" not in caplog.text
    assert events(caplog)[0]["model"] == "unknown"


def test_console_and_file_append_without_enabling_http_logs(tmp_path, capsys):
    logger = logging.getLogger(LOGGER_NAME)
    previous = logger.handlers[:], logger.level, logger.propagate
    root = logging.getLogger()
    root_state = root.handlers[:], root.level
    session = tmp_path / "worker-session.json"
    for text in ("first event", "second event"):
        with worker_logging(session) as path:
            logger.info(text)
    assert "first event" in path.read_text()
    assert "second event" in path.read_text()
    assert "second event" in capsys.readouterr().out
    assert path.stat().st_mode & 0o777 == 0o600
    assert (logger.handlers, logger.level, logger.propagate) == previous
    assert (root.handlers, root.level) == root_state


def test_rotation_is_bounded_and_private(tmp_path):
    path = tmp_path / "worker.log"
    handler = PrivateRotatingFileHandler(path, maxBytes=100, backupCount=3)
    try:
        for i in range(20):
            handler.emit(
                logging.LogRecord(
                    LOGGER_NAME, logging.INFO, "", 0, "event %s " + "x" * 50, (i,), None
                )
            )
    finally:
        handler.close()
    files = list(tmp_path.glob("worker.log*"))
    assert len(files) == 4
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert all(p.stat().st_size <= 100 for p in files)


@pytest.mark.parametrize("filename", ["session.log", "session.LOG", "session"])
def test_custom_session_filename_is_never_used_as_the_log(tmp_path, filename):
    session = tmp_path / filename
    session.write_text("private-session")
    session.chmod(0o600)
    with worker_logging(session) as path:
        logging.getLogger(LOGGER_NAME).info("runtime event")
    assert path.name == filename + ".log"
    assert "runtime event" in path.read_text()
    assert session.read_text() == "private-session"


@pytest.mark.parametrize("kind", ["symlink", "public", "hardlink"])
def test_unsafe_existing_log_is_not_written(tmp_path, kind):
    target = tmp_path / "target"
    target.write_text("untouched")
    target.chmod(0o600)
    path = tmp_path / "worker-session.log"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    else:
        path.write_text("untouched")
        path.chmod(0o644)
    with pytest.raises((OSError, ValueError)):
        with worker_logging(tmp_path / "worker-session.json"):
            pytest.fail("unsafe log accepted")
    assert target.read_text() == "untouched"
    assert path.read_text() == "untouched"


def test_startup_enables_logging_for_all_worker_entrypoints(
    tmp_path, monkeypatch, capsys
):
    from oshepherd.backends.supabase import startup

    async def run(config, **kwargs):
        logging.getLogger(LOGGER_NAME).info("worker runtime event")

    monkeypatch.setattr(startup, "_run_worker", run)
    config = SimpleNamespace(
        WORKER_SESSION_FILE=tmp_path / "local.worker-session.json", LOGLEVEL="info"
    )
    asyncio.run(startup.start_worker(config))
    assert "worker runtime event" in capsys.readouterr().out
    assert "worker runtime event" in (tmp_path / "local.worker-session.log").read_text()
