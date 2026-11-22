"""Externally running Ollama worker with independent control/inference tasks."""

import asyncio
from contextlib import aclosing
from datetime import datetime
import hashlib
import json
import logging
import re
import time
from uuid import UUID

import httpx
import ollama

from oshepherd.common.protocol import (
    PAGE_BYTES,
    RECORD_BYTES,
    record_bytes,
    utf8_fragments,
)
from oshepherd.worker.executor import IncompleteOutputError, OutputTooLargeError
from .client import RpcError

logger = logging.getLogger(__name__)

LOG_ERROR_CODES = frozenset(
    {
        "stale_attempt",
        "stale_session",
        "worker_revoked",
        "backend_unavailable",
        "backend_error",
        "protocol_mismatch",
        "invalid_rpc_response",
        "storage_unavailable",
        "storage_not_configured",
        "execution_expired",
        "output_too_large",
        "incomplete_upstream_output",
        "upstream_unavailable",
        "upstream_error",
        "invalid_upstream_output",
    }
)


def log_identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return "unknown"


class SupabaseWorker:
    """The RPC client has only a publishable key plus the enrolled worker JWT.

    Inference awaits never block heartbeat, model inventory or token refresh.
    Closing the worker cancels its reads and reports bounded classified errors;
    the database remains authoritative if any acknowledgement is ambiguous.
    """

    def __init__(
        self,
        rpc,
        executor,
        *,
        concurrency=1,
        heartbeat_seconds=10,
        poll_seconds=1,
        inventory_seconds=30,
        drain_seconds=20,
        session=None,
        auth=None,
        session_file=None,
        systemone=False,
        result_storage=None,
    ):
        if not 1 <= concurrency <= 64 or not 0 < heartbeat_seconds <= 10:
            raise ValueError("invalid_worker_capacity")
        if not 0 < poll_seconds <= heartbeat_seconds or not 0 < inventory_seconds <= 30:
            raise ValueError("invalid_worker_interval")
        self.rpc, self.executor = rpc, executor
        self.concurrency = concurrency
        self.interval = min(heartbeat_seconds, poll_seconds)
        self.inventory_seconds = inventory_seconds
        self.drain_seconds = drain_seconds
        self.auth, self.auth_session, self.session_file = auth, session, session_file
        if auth is not None and (session is None or session_file is None):
            raise ValueError("refresh_requires_private_session_file")
        self.systemone = systemone
        self.result_storage = result_storage
        self.cluster_id = None
        self.worker_id = None
        self.session = None
        self.jobs = {}
        self.ready = asyncio.Event()
        self.draining = False

    async def register(self):
        registered = await self.rpc.rpc("register_worker", {})
        if self.concurrency > registered["capacity"]:
            raise ValueError("configured_concurrency_exceeds_enrollment")
        self.session = {key: registered[key] for key in ("session_id", "generation")}
        self.cluster_id = registered["cluster_id"]
        self.worker_id = registered.get("worker_id")
        await self.inventory()
        logger.info(
            json.dumps(
                {
                    "event": "worker_registered",
                    "worker_id": log_identifier(self.worker_id),
                    "capacity": self.concurrency,
                }
            )
        )
        self.ready.set()

    async def inventory(self):
        # A large inventory or trickling responses must also have a total bound,
        # rather than multiplying the per-request deadline by up to 256 models.
        async with asyncio.timeout(25):
            await self._inventory()

    async def _inventory(self):
        tags = await self.executor.metadata("tags")
        running = await self.executor.metadata("ps")
        version = await self.executor.metadata("version")
        models = []
        if not isinstance(tags.get("models"), list) or len(tags["models"]) > 256:
            raise ValueError("invalid_model_inventory")
        for model in tags["models"]:
            if (
                not isinstance(model, dict)
                or not isinstance(model.get("name"), str)
                or not 1 <= len(model["name"]) <= 256
                or not isinstance(model.get("digest"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", model["digest"])
            ):
                raise ValueError("invalid_model_inventory")
            details = await self.executor.metadata("show", model["name"])
            capabilities = details.get("capabilities", [])
            if not isinstance(capabilities, list) or any(
                not isinstance(capability, str) for capability in capabilities
            ):
                raise ValueError("invalid_model_inventory")
            operations = []
            # Older Ollama show responses omit capabilities; generation/chat
            # remain their historical default. Explicit embedding-only models
            # are never advertised for completion.
            if not capabilities or "completion" in capabilities:
                operations.extend(["generate", "chat"])
            if "embedding" in capabilities:
                operations.append("embeddings")
            if self.systemone or "decision" in capabilities:
                operations.append("systemone")
            models.append(
                {
                    "name": model["name"],
                    "digest": model["digest"],
                    "operations": operations,
                    "details": model,
                    "show": {"model_info": details},
                }
            )
        await self.rpc.rpc(
            "update_models",
            {**self.session, "models": models, "running": running, "version": version},
        )

    def active(self):
        return [
            {"job_id": job_id, "attempt_id": claim["attempt_id"]}
            for job_id, (claim, task) in self.jobs.items()
            if not task.done()
        ]

    async def control_once(self):
        for job_id, (_, task) in list(self.jobs.items()):
            if task.done():
                # Retrieve unexpected exceptions, with no prompt/token logging.
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()
                del self.jobs[job_id]
        response = await self.rpc.rpc(
            "heartbeat_and_claim",
            {
                **self.session,
                "active_attempts": self.active(),
                "max_jobs": max(0, self.concurrency - len(self.jobs)),
                "draining": self.draining,
            },
        )
        for stopped in response["stop"]:
            pair = self.jobs.get(stopped["job_id"])
            if pair and pair[0]["attempt_id"] == stopped["attempt_id"]:
                pair[1].cancel()
        for claim in response["jobs"]:
            existing = self.jobs.get(claim["job_id"])
            if existing:
                if existing[0]["attempt_id"] != claim["attempt_id"]:
                    raise RpcError("protocol_mismatch")
                continue
            if len(self.jobs) >= self.concurrency:
                raise RpcError("protocol_mismatch")
            self.log_job("job_claimed", claim)
            self.jobs[claim["job_id"]] = (
                claim,
                asyncio.create_task(self.execute(claim, response["server_now"])),
            )

    def log_job(self, event, claim, *, elapsed=None, error_code=None):
        # Only explicit metadata is logged, never payloads, output or exceptions.
        request = claim.get("request", {})
        if not isinstance(request, dict):
            request = {}
        operation = request.get("type")
        payload = request.get("payload", {})
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}", model
        ):
            model = "unknown"
        data = {
            "event": event,
            "worker_id": log_identifier(self.worker_id),
            "job_id": log_identifier(claim.get("job_id")),
            "attempt_id": log_identifier(claim.get("attempt_id")),
            "operation": (
                operation
                if operation in ("generate", "chat", "embed", "embeddings", "systemone")
                else "unknown"
            ),
            "model": model,
        }
        if elapsed is not None:
            data["duration_ms"] = round(elapsed * 1000)
        if error_code is not None:
            data["error_code"] = (
                error_code if error_code in LOG_ERROR_CODES else "internal_error"
            )
        level = (
            logging.WARNING
            if event in ("attempt_failed", "attempt_abandoned", "attempt_unconfirmed")
            else logging.INFO
        )
        logger.log(level, json.dumps(data, ensure_ascii=True))

    async def execute(self, claim, server_now):
        started = time.monotonic()
        self.log_job("job_started", claim)
        event, error_code = "attempt_unconfirmed", None
        try:
            event, error_code = await self._execute(claim, server_now)
        except asyncio.CancelledError:
            event = "attempt_cancelled"
            raise
        except RpcError as error:
            error_code = error.code
            raise
        except Exception:
            error_code = "internal_error"
            raise
        finally:
            self.log_job(
                event, claim, elapsed=time.monotonic() - started, error_code=error_code
            )

    async def _execute(self, claim, server_now):
        fields = {
            **self.session,
            **{key: claim[key] for key in ("job_id", "attempt_id")},
        }
        deadline = datetime.fromisoformat(
            claim["execution_deadline"].replace("Z", "+00:00")
        )
        observed = datetime.fromisoformat(server_now.replace("Z", "+00:00"))
        budget = max(0, (deadline - observed).total_seconds())
        try:
            async with asyncio.timeout(budget):
                operation, payload = (
                    claim["request"]["type"],
                    claim["request"]["payload"],
                )
                if operation in ("generate", "chat") and payload.get("stream") is True:
                    sequence, total, final = 0, 0, None
                    async with aclosing(
                        self.executor.iter_records(operation, payload)
                    ) as records:
                        async for record in records:
                            content = record_bytes(record)
                            total += len(content)
                            if (
                                len(content) > RECORD_BYTES
                                or total > claim["max_output_bytes"]
                            ):
                                raise OutputTooLargeError(
                                    "serialized output exceeds budget"
                                )
                            fragments = [
                                fragment.decode()
                                for fragment in utf8_fragments(content)
                            ]
                            if record.get("done") is True:
                                final = fragments
                                break
                            for fragment in fragments:
                                sequence += 1
                                await self.rpc.rpc(
                                    "append_output",
                                    {
                                        **fields,
                                        "seq": sequence,
                                        "content": fragment,
                                        "checksum": hashlib.sha256(
                                            fragment.encode()
                                        ).hexdigest(),
                                    },
                                )
                    if final is None:
                        raise IncompleteOutputError("terminal record missing")
                    await self.rpc.rpc(
                        "finish_job", {**fields, "final_chunks": final, "result": None}
                    )
                else:
                    result = await self.executor.complete(operation, payload)
                    content = json.dumps(result).encode()
                    if len(content) > claim["max_output_bytes"]:
                        raise OutputTooLargeError("result exceeds budget")
                    if len(content) > PAGE_BYTES or claim.get("mode") == "async":
                        if self.result_storage is None:
                            raise RpcError("storage_not_configured")
                        descriptor = await self.result_storage.upload(
                            self.cluster_id, claim, content
                        )
                    else:
                        descriptor = {
                            "inline": content.decode(),
                            "object_path": None,
                            "size_bytes": len(content),
                            "checksum": hashlib.sha256(content).hexdigest(),
                            "content_type": "application/json",
                        }
                    await self.rpc.rpc(
                        "finish_job",
                        {**fields, "final_chunks": [], "result": descriptor},
                    )
        except asyncio.CancelledError:
            raise
        except RpcError as error:
            if error.code in ("stale_attempt", "stale_session", "worker_revoked"):
                return "attempt_abandoned", error.code
            # An ambiguous append/finish is left for SQL lease recovery. Never
            # classify it as an inference failure and accidentally execute again.
            if error.code in (
                "backend_unavailable",
                "backend_error",
                "protocol_mismatch",
                "invalid_rpc_response",
            ):
                raise
            return await self.fail(
                fields, error.code, error.code == "storage_unavailable"
            )
        except TimeoutError:
            return await self.fail(fields, "execution_expired", False)
        except OutputTooLargeError:
            return await self.fail(fields, "output_too_large", False)
        except IncompleteOutputError:
            return await self.fail(fields, "incomplete_upstream_output", True)
        except httpx.TransportError:
            return await self.fail(fields, "upstream_unavailable", True)
        except ollama.ResponseError as error:
            return await self.fail(fields, "upstream_error", error.status_code >= 500)
        except (ValueError, KeyError, TypeError):
            return await self.fail(fields, "invalid_upstream_output", False)
        return "job_succeeded", None

    async def fail(self, fields, code, retryable):
        try:
            await self.rpc.rpc(
                "fail_attempt",
                {
                    **fields,
                    "error": {"code": code, "message": "Local inference failed"},
                    "retryable": retryable,
                },
            )
        except RpcError as error:
            if error.code not in ("stale_attempt", "stale_session", "worker_revoked"):
                raise
            return "attempt_abandoned", error.code
        return "attempt_failed", code

    async def inventory_loop(self, stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), self.inventory_seconds)
            except TimeoutError:
                # Do not overwrite a good inventory on a transient local outage.
                try:
                    await self.inventory()
                except (
                    TimeoutError,
                    httpx.HTTPError,
                    ollama.ResponseError,
                    ValueError,
                    KeyError,
                ):
                    logger.warning("Ollama inventory unavailable")

    async def refresh_loop(self, stop):
        while not stop.is_set():
            wait = max(1, self.auth_session.expires_at - time.time() - 60)
            try:
                await asyncio.wait_for(stop.wait(), wait)
            except TimeoutError:
                session = await self.auth.refresh(self.auth_session)
                session.save(self.session_file)
                self.auth_session = session
                self.rpc.access_token = session.access_token.get_secret_value()

    async def run(self, stop):
        await self.register()
        background_stop = asyncio.Event()
        tasks = [asyncio.create_task(self.inventory_loop(background_stop))]
        if self.auth is not None:
            tasks.append(asyncio.create_task(self.refresh_loop(background_stop)))
        try:
            while not stop.is_set():
                for task in tasks:
                    if task.done():
                        if not task.cancelled() and task.exception():
                            raise task.exception()
                        raise RpcError("control_task_stopped")
                await self.control_once()
                try:
                    await asyncio.wait_for(stop.wait(), self.interval)
                except TimeoutError:
                    pass
            self.draining = True
            await self.control_once()
            until = asyncio.get_running_loop().time() + self.drain_seconds
            while self.jobs and asyncio.get_running_loop().time() < until:
                await self.control_once()
                await asyncio.sleep(
                    min(
                        self.interval, max(0, until - asyncio.get_running_loop().time())
                    )
                )
        finally:
            background_stop.set()
            for _, task in self.jobs.values():
                task.cancel()
            await asyncio.gather(
                *(task for _, task in self.jobs.values()), return_exceptions=True
            )
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.jobs.clear()
