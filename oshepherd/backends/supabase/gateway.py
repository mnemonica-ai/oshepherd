"""Request-owned FastAPI gateway; no collectors, Celery, Redis or startup I/O."""

import asyncio
import hashlib
import json
import re
import time
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import Response, StreamingResponse

from oshepherd.common.protocol import (
    CHUNK_BYTES,
    INFERENCE_PATHS,
    PAGE_BYTES,
    REQUEST_BYTES,
    normalize_request,
)
from .client import RpcClient, RpcError
from .gateway_storage import GatewayResultStorage

TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
PRIVATE = {"Cache-Control": "private, no-store"}


def json_response(value, status=200, headers=None):
    return Response(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
        status_code=status,
        media_type="application/json",
        headers={**PRIVATE, **(headers or {})},
    )


def job_status(job):
    return {
        "id": job["job_id"],
        "status": job["state"],
        "attempt": job["attempts"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "execution_deadline": job["execution_deadline"],
        "error": job["error"],
        "result": {
            "available": job["state"] == "succeeded" and job["content_available"],
            "size_bytes": job["output_bytes"] if job["state"] == "succeeded" else None,
            "checksum": (job.get("result") or {}).get("checksum"),
            "expires_at": job["content_expires_at"],
        },
    }


class GatewayRequest:
    def __init__(self, config, rpc):
        self.config, self.rpc = config, rpc
        self.storage = GatewayResultStorage(rpc)
        self.caller = None
        self.job_id = None
        self.token = None
        self.complete = False
        self.reason = "cancelled"
        self.deadline = time.monotonic() + config.OSHEPHERD_SYNC_SECONDS

    async def close(self):
        # ASGI disconnect cancellation must not cancel its own SQL cleanup.
        with anyio.move_on_after(3, shield=True):
            if self.job_id and self.token and not self.complete:
                try:
                    await self.cancel(self.job_id, self.token, self.reason)
                except RpcError:
                    pass  # SQL consumer/absolute deadlines remain authoritative.
        with anyio.move_on_after(2, shield=True):
            await self.rpc.close()

    async def read(self, job_id, cursor=0, token=None):
        page = await self.rpc.rpc(
            "read_updates",
            {
                **self.caller,
                "cursors": [
                    {"job_id": job_id, "cursor": cursor, "consumer_token": token}
                ],
                "max_bytes": PAGE_BYTES,
                "max_chunks": 64,
            },
        )
        if not isinstance(page.get("jobs"), list) or len(page["jobs"]) != 1:
            raise RpcError("protocol_mismatch")
        return page["jobs"][0]

    async def cancel(self, job_id, token, reason="cancelled"):
        return await self.rpc.rpc(
            "cancel_job",
            {
                **self.caller,
                "job_id": job_id,
                "consumer_token": token,
                "reason": reason,
            },
        )

    async def result(self, job):
        if not job["content_available"]:
            raise RpcError("result_expired", 410)
        descriptor = job.get("result")
        if job.get("result_omitted"):
            raise RpcError("result_too_large", 413)
        if not isinstance(descriptor, dict):
            raise RpcError("invalid_result")
        limit = self.config.OSHEPHERD_RESPONSE_BYTES
        if descriptor.get("object_path"):
            content = await self.storage.read(descriptor, limit=limit)
        else:
            if not isinstance(descriptor.get("inline"), str):
                raise RpcError("invalid_result")
            content = descriptor["inline"].encode()
            if len(content) > limit:
                raise RpcError("result_too_large", 413)
            if len(content) != descriptor.get("size_bytes") or hashlib.sha256(
                content
            ).hexdigest() != descriptor.get("checksum"):
                raise RpcError("invalid_result")
        return Response(content, media_type="application/json", headers=PRIVATE)

    async def chunks(self):
        cursor, sent = 0, 0
        try:
            while True:
                page = await self.read(self.job_id, cursor, self.token)
                chunks = page.get("chunks")
                if not isinstance(chunks, list):
                    raise RpcError("protocol_mismatch")
                for chunk in chunks:
                    if chunk.get("seq") != cursor + 1 or not isinstance(
                        chunk.get("content"), str
                    ):
                        raise RpcError("sequence_gap")
                    content = chunk["content"].encode()
                    sent += len(content)
                    if len(content) > CHUNK_BYTES or hashlib.sha256(
                        content
                    ).hexdigest() != chunk.get("checksum"):
                        raise RpcError("invalid_chunk")
                    if sent > self.config.OSHEPHERD_RESPONSE_BYTES:
                        raise RpcError("result_too_large", 413)
                    cursor = chunk["seq"]
                    yield content
                if page["state"] in TERMINAL and cursor == page["last_seq"]:
                    self.complete = True
                    if page["state"] != "succeeded":
                        yield (
                            json.dumps(
                                {
                                    "error": (page.get("error") or {}).get(
                                        "code", page["state"]
                                    )
                                }
                            )
                            + "\n"
                        ).encode()
                    return
                if cursor < page["last_seq"] and not chunks:
                    raise RpcError("sequence_gap")
                await asyncio.sleep(0.25)
        except RpcError as error:
            yield (json.dumps({"error": error.code}) + "\n").encode()


class OwnedStream(StreamingResponse):
    def __init__(self, owner):
        self.owner = owner
        super().__init__(
            owner.chunks(), media_type="application/x-ndjson", headers=PRIVATE
        )

    async def stream_response(self, send):
        async def bounded_send(message):
            try:
                with anyio.fail_after(self.owner.config.OSHEPHERD_READER_STALL_SECONDS):
                    await send(message)
            except TimeoutError:
                self.owner.reason = "reader_stalled"
                raise

        await super().stream_response(bounded_send)

    async def __call__(self, scope, receive, send):
        try:
            with anyio.fail_after(max(0, self.owner.deadline - time.monotonic())):
                await super().__call__(scope, receive, send)
        except TimeoutError:
            if self.owner.reason != "reader_stalled":
                self.owner.reason = "execution_expired"
        finally:
            with anyio.move_on_after(3, shield=True):
                await self.body_iterator.aclose()
            await self.owner.close()


async def parse_body(request):
    body = bytearray()
    async for fragment in request.stream():
        if len(body) + len(fragment) > REQUEST_BYTES:
            raise RpcError("request_too_large", 413)
        body.extend(fragment)
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError:
        raise RpcError("invalid_request", 422) from None


def normalize(operation, payload, *, asynchronous=False):
    try:
        return normalize_request(operation, payload, asynchronous=asynchronous)
    except ValueError as error:
        code = (
            "async_stream_not_supported"
            if str(error) == "async_stream_not_supported"
            else "invalid_request"
        )
        raise RpcError(code, 422) from None


def create_supabase_app(config, *, rpc_factory=None):
    # Validate configuration outside request processing; construction performs
    # no network I/O and acquires no HTTP client or background task.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    origins = [
        origin for origin in config.OSHEPHERD_ALLOWED_ORIGINS.split(",") if origin
    ]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )
    if rpc_factory is None:
        rpc_factory = lambda: RpcClient(
            config.SUPABASE_URL,
            config.SUPABASE_SECRET_KEY.get_secret_value(),
            schema=config.SUPABASE_SCHEMA,
        )

    async def handle(request: Request):
        owner = GatewayRequest(config, rpc_factory())
        detached = False
        try:
            bearer = re.fullmatch(
                r"Bearer (osk_[A-Za-z0-9_-]{43})",
                request.headers.get("Authorization", ""),
                re.IGNORECASE,
            )
            if not bearer:
                raise RpcError("unauthorized", 401)
            owner.caller = {
                "cluster_id": str(config.OSHEPHERD_CLUSTER_ID),
                "key": bearer[1],
            }
            await owner.rpc.rpc("backend_info", owner.caller)
            path = request.url.path
            method = request.method
            if path == "/health" and method == "GET":
                return json_response({"status": 200})
            metadata = re.fullmatch(r"/api/(tags|ps|show|version)", path)
            if metadata:
                operation = metadata[1]
                if method != ("POST" if operation == "show" else "GET"):
                    raise RpcError("method_not_allowed", 405)
                model = (
                    normalize("show", await parse_body(request))["payload"]["model"]
                    if operation == "show"
                    else None
                )
                result = await owner.rpc.rpc(
                    "read_metadata",
                    {**owner.caller, "operation": operation, "model": model},
                )
                return json_response(result["response"])
            job_path = re.fullmatch(r"/jobs/([^/]+)(/result)?", path)
            if job_path:
                try:
                    job_id = str(UUID(job_path[1]))
                except ValueError:
                    raise RpcError("not_found", 404) from None
                if method != "GET" and not (method == "DELETE" and not job_path[2]):
                    raise RpcError("method_not_allowed", 405)
                job = await owner.read(job_id)
                if job["mode"] != "async":
                    raise RpcError("not_found", 404)
                if method == "DELETE":
                    return json_response(job_status(await owner.cancel(job_id, None)))
                if not job_path[2]:
                    return json_response(job_status(job))
                if job["state"] not in TERMINAL:
                    return json_response({"id": job_id, "status": job["state"]}, 202)
                if not job["content_available"]:
                    raise RpcError("result_expired", 410)
                if job["state"] != "succeeded":
                    return json_response(
                        {"error": (job.get("error") or {}).get("code", job["state"])},
                        409,
                    )
                if request.query_params.get("download") == "true":
                    if not (job.get("result") or {}).get("object_path"):
                        raise RpcError("storage_not_configured")
                    return json_response(
                        await owner.storage.sign(
                            job["result"], job["content_expires_at"]
                        )
                    )
                return await owner.result(job)
            asynchronous = path == "/jobs"
            operation = INFERENCE_PATHS.get(path)
            if not asynchronous and not operation:
                raise RpcError("not_found", 404)
            if method != "POST":
                raise RpcError("method_not_allowed", 405)
            body = await parse_body(request)
            if asynchronous:
                if (
                    set(body) - {"endpoint", "payload", "limits"}
                    or body.get("endpoint") not in INFERENCE_PATHS
                ):
                    raise RpcError("invalid_request", 422)
                normalized = normalize(
                    INFERENCE_PATHS[body["endpoint"]],
                    body.get("payload"),
                    asynchronous=True,
                )
                limits = body.get("limits", {})
                if not isinstance(limits, dict):
                    raise RpcError("invalid_request", 422)
            else:
                normalized = normalize(operation, body)
                limits = {
                    "total_ms": int(config.OSHEPHERD_SYNC_SECONDS * 1000),
                    "max_output_bytes": config.OSHEPHERD_RESPONSE_BYTES,
                }
            idempotency = request.headers.get("Idempotency-Key")
            if idempotency is not None and not 1 <= len(idempotency) <= 128:
                raise RpcError("invalid_request", 422)
            owner.token = None if asynchronous else str(uuid4())
            # Resolve admission's nonce even if the caller disconnects midway.
            # Accepted async work survives; accepted sync work gets cancelled
            # by the request's owned cleanup if its response cannot be delivered.
            job = await owner.rpc.rpc(
                "submit_job",
                {
                    **owner.caller,
                    "request": normalized,
                    "mode": "async" if asynchronous else "sync",
                    "limits": limits,
                    "idempotency_key": idempotency,
                    "consumer_token": owner.token,
                },
            )
            owner.job_id = str(UUID(job["job_id"]))
            if asynchronous:
                status_url = (
                    (config.OSHEPHERD_PUBLIC_URL or str(request.base_url).rstrip("/"))
                    + "/jobs/"
                    + owner.job_id
                )
                return json_response(
                    {
                        "id": owner.job_id,
                        "status": job["state"],
                        "status_url": status_url,
                    },
                    202,
                    {"Location": status_url},
                )
            if (
                operation in ("generate", "chat")
                and normalized["payload"]["stream"] is True
            ):
                detached = True
                return OwnedStream(owner)
            with anyio.fail_after(max(0, owner.deadline - time.monotonic())):
                while True:
                    if await request.is_disconnected():
                        raise RpcError("cancelled", 499)
                    job = await owner.read(owner.job_id, token=owner.token)
                    if job["state"] in TERMINAL:
                        owner.complete = True
                        if job["state"] != "succeeded":
                            raise RpcError(
                                (job.get("error") or {}).get("code", "upstream_error"),
                                504 if job["state"] == "expired" else 500,
                            )
                        return await owner.result(job)
                    await asyncio.sleep(0.25)
        except TimeoutError:
            owner.reason = "execution_expired"
            return json_response({"error": "execution_expired"}, 504)
        except (ClientDisconnect, asyncio.CancelledError):
            raise
        except RpcError as error:
            return json_response(
                {"error": error.code},
                error.status,
                {"Retry-After": "1"} if error.status == 429 else None,
            )
        except (ValueError, KeyError, TypeError):
            return json_response({"error": "protocol_mismatch"}, 503)
        finally:
            if not detached:
                await owner.close()

    app.add_api_route(
        "/{path:path}",
        handle,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    return app
