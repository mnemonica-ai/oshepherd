"""Resumable foreground sharing over the existing operator/worker lifecycles."""

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal

from .config import SupabaseMaintenanceConfig, SupabaseWorkerConfig
from .maintenance import run_maintenance
from .operator import Installation
from .private_files import (
    private_directory,
    read_private_json,
    write_private,
    write_private_json,
)
from .releases import OperatorError
from .sessions import WorkerSession
from .startup import login_worker, read_enrollment, start_worker


@contextmanager
def sharing_lock(directory):
    """One foreground owner per local state; operator transactions keep their lock."""
    directory = private_directory(directory)
    descriptor = os.open(
        directory / "share.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperatorError("sharing_already_running") from None
        yield
    finally:
        os.close(descriptor)


def gateway_url(config):
    gateway = config.gateway_url or (
        config.supabase_url + "/functions/v1/ollama"
        if config.profile == "edge_free"
        else None
    )
    if gateway is None:
        raise OperatorError("sharing_gateway_required")
    return gateway


def bind_sharing(operator, *, owner_subject, worker_name, capacity, ollama_url):
    """Persist the selected nonsecret target before any provider/SQL write."""
    path = operator.directory / "share.json"
    selection = {
        "v": 1,
        "project_ref": operator.config.project_ref,
        "supabase_url": operator.config.supabase_url,
        "profile": operator.config.profile,
        "gateway_url": gateway_url(operator.config),
        "owner_subject": str(owner_subject),
        "worker_name": worker_name,
        "capacity": capacity,
        "ollama_url": ollama_url,
    }
    if path.exists():
        if read_private_json(path) != selection:
            raise OperatorError("sharing_configuration_mismatch")
    else:
        write_private_json(path, selection)


class _Stopped(Exception):
    pass


async def _guard(awaitable, services, stopped, *, timeout=None):
    """A completed child, even with no exception, cannot masquerade as readiness."""
    operation = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait(
            [operation, stopped, *services],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stopped in done:
            raise _Stopped()
        for service, name in services.items():
            if service in done:
                # Retrieve the exception, but never expose potentially secret
                # network/configuration exception text in the user-facing error.
                if not service.cancelled():
                    service.exception()
                raise OperatorError("sharing_" + name + "_stopped")
        if operation not in done:
            raise OperatorError("sharing_readiness_timeout")
        return operation.result()
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def _supervise(operator, worker_config, *, model, check, stop, on_event):
    worker_ready, maintenance_ready = asyncio.Event(), asyncio.Event()
    state = Installation.load(operator.directory, operator.config)
    maintenance_config = SupabaseMaintenanceConfig(
        SUPABASE_URL=operator.config.supabase_url,
        SUPABASE_SECRET_KEY=operator.config.secret_key,
        OSHEPHERD_CLUSTER_ID=state.cluster_id,
    )
    services = {
        asyncio.create_task(
            start_worker(worker_config, stop=stop, ready=worker_ready)
        ): "worker",
        asyncio.create_task(
            run_maintenance(
                maintenance_config,
                stop=stop,
                ready=maintenance_ready,
                fail_fast=True,
            )
        ): "maintenance",
    }
    stopped = asyncio.create_task(stop.wait())
    try:
        await _guard(
            asyncio.gather(worker_ready.wait(), maintenance_ready.wait()),
            services,
            stopped,
            timeout=60,
        )
        on_event("worker_connected")
        on_event("maintenance_ready")
        result = await _guard(
            operator.verify_inference(model), services, stopped, timeout=140
        )
        if result.get("inference_verified") is not True:
            raise OperatorError("sharing_inference_not_verified")
        on_event("inference_verified")
        result.update(
            gateway_url=gateway_url(operator.config),
            client_file=str(operator.directory / "client.json"),
            foreground=not check,
            hosted_qualified=False,
        )
        if not check:
            on_event("sharing_foreground")
            try:
                await _guard(stop.wait(), services, stopped)
            except _Stopped:
                pass
        return result
    except _Stopped:
        return {"stopped": True, "inference_verified": False, "hosted_qualified": False}
    finally:
        stop.set()
        # Give the existing worker its bounded drain period before forcefully
        # cancelling; cleanup has a bounded thirty-second cycle.
        _, pending = await asyncio.wait(services, timeout=65)
        for task in pending:
            task.cancel()
        await asyncio.gather(*services, return_exceptions=True)
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)


async def run_share(
    operator,
    *,
    model,
    ollama_url,
    owner_subject=None,
    worker_name="local",
    capacity=1,
    deploy=None,
    check=False,
    renew_enrollment=False,
    on_event=lambda event: None,
    stop=None,
):
    """Resume a selected installation and keep its worker and cleanup supervised.

    The caller performs local model discovery and explicit provider selection.
    No project is selected, adopted, created or removed implicitly here.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", worker_name):
        raise OperatorError("invalid_worker_enrollment")
    if type(capacity) is not int or not 1 <= capacity <= 64:
        raise OperatorError("invalid_worker_enrollment")
    if not isinstance(model, str) or not 1 <= len(model) <= 256:
        raise OperatorError("sharing_model_required")
    # Validate before writing intent or calling install.
    from .onboarding import validate_ollama_url

    ollama_url = validate_ollama_url(ollama_url)
    with sharing_lock(operator.directory):
        state = None
        if (operator.directory / "installation.json").exists():
            state = Installation.load(operator.directory, operator.config)
            if state.stage == "disabled":
                raise OperatorError("installation_disabled")
            if state.rotation is not None:
                raise OperatorError("pending_key_rotation_use_rotate_key")
            if owner_subject is not None and owner_subject != state.owner_subject:
                raise OperatorError("installation_owner_mismatch")
            owner_subject = state.owner_subject
        if owner_subject is None:
            raise OperatorError("sharing_owner_required_for_initial_install")
        bind_sharing(
            operator,
            owner_subject=owner_subject,
            worker_name=worker_name,
            capacity=capacity,
            ollama_url=ollama_url,
        )
        if state is None or state.stage != "configured":
            await operator.install(owner_subject)
        if deploy is not None:
            await deploy()
        readiness = await operator.doctor()
        if not readiness["enabled"]:
            raise OperatorError("installation_disabled")
        if not readiness["schema_matches"]:
            raise OperatorError("sharing_schema_upgrade_required")
        if not readiness["gateway_available"]:
            raise OperatorError("sharing_gateway_unavailable_deploy_required")
        on_event("cloud_ready")
        state = Installation.load(operator.directory, operator.config)
        session_file = operator.directory / (worker_name + ".worker-session.json")
        if session_file.exists():
            enrollment = state.workers.get(worker_name)
            if (
                enrollment is None
                or enrollment.revocation_requested
                or enrollment.capacity != capacity
                or renew_enrollment
            ):
                raise OperatorError("sharing_worker_session_conflict")
            session = WorkerSession.load(
                session_file, supabase_url=operator.config.supabase_url
            )
            if session.user_id != enrollment.auth_subject:
                raise OperatorError("sharing_worker_session_conflict")
        else:
            await operator.enroll(worker_name, capacity, renew=renew_enrollment)
        worker_config = SupabaseWorkerConfig(
            SUPABASE_URL=operator.config.supabase_url,
            SUPABASE_PUBLISHABLE_KEY=operator.config.publishable_key,
            WORKER_SESSION_FILE=session_file,
            OLLAMA_BASE_URL=ollama_url,
            CONCURRENCY=capacity,
        )
        if not session_file.exists():
            token = read_enrollment(
                operator.directory / (worker_name + ".enrollment.json"),
                supabase_url=operator.config.supabase_url,
            )
            login_journal = operator.directory / (worker_name + ".share-login.json")
            attempt = {
                "v": 1,
                "supabase_url": operator.config.supabase_url,
                "worker_name": worker_name,
                "token_digest": hashlib.sha256(token.encode()).hexdigest(),
            }
            if login_journal.exists():
                previous = read_private_json(login_journal)
                if previous == attempt:
                    raise OperatorError("sharing_enrollment_ambiguous_use_renew")
                if (
                    not isinstance(previous, dict)
                    or previous.get("v") != 1
                    or previous.get("supabase_url") != operator.config.supabase_url
                    or previous.get("worker_name") != worker_name
                ):
                    raise OperatorError("sharing_enrollment_journal_mismatch")
            # Persist before the single-use Auth exchange. If the server consumes
            # it but the reply/session write is lost, require explicit renewal.
            write_private_json(login_journal, attempt)
            await login_worker(
                worker_config,
                token,
            )
            state = Installation.load(operator.directory, operator.config)
            enrollment = state.workers.get(worker_name)
            session = WorkerSession.load(
                session_file, supabase_url=operator.config.supabase_url
            )
            if (
                enrollment is None
                or enrollment.revocation_requested
                or enrollment.capacity != capacity
                or session.user_id != enrollment.auth_subject
            ):
                raise OperatorError("sharing_worker_session_conflict")
        # Keep the worker-only escape hatch accurate for an explicitly chosen
        # Ollama origin. Never include operator or application credentials.
        worker_values = {
            "SUPABASE_URL": worker_config.SUPABASE_URL,
            "SUPABASE_PUBLISHABLE_KEY": operator.config.publishable_key.get_secret_value(),
            "WORKER_SESSION_FILE": str(session_file),
            "CONCURRENCY": str(capacity),
            "OLLAMA_BASE_URL": ollama_url,
        }
        write_private(
            operator.directory / (worker_name + ".worker.env"),
            "".join(
                f"{key}={json.dumps(value)}\n" for key, value in worker_values.items()
            ),
        )
        owns_signals = stop is None
        stop = stop if stop is not None else asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = []
        try:
            if owns_signals:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(signum, stop.set)
                    signals.append(signum)
            return await _supervise(
                operator,
                worker_config,
                model=model,
                check=check,
                stop=stop,
                on_event=on_event,
            )
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)
