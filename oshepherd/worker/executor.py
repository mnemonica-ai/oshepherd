"""Local inference without broker, lease, enrollment, or publishing concerns."""

import asyncio
import json
from typing import AsyncIterator

import httpx
import ollama
from ollama._types import ChatRequest, EmbeddingsRequest, GenerateRequest

from oshepherd.common.ollama import serialize_ollama_res
from oshepherd.common.protocol import INFERENCE_PATHS, RECORD_BYTES, RESULT_BYTES


class IncompleteOutputError(RuntimeError):
    code = "incomplete_upstream_output"


class OutputTooLargeError(RuntimeError):
    code = "output_too_large"


class LegacyOllamaExecutor:
    """Existing SDK/host/error semantics retained for the Redis adapter."""

    def __init__(self, sdk=ollama):
        self.sdk = sdk

    def complete(self, operation, payload):
        if operation in ("generate", "chat"):
            return serialize_ollama_res(getattr(self.sdk, operation)(**payload))
        if operation == "embeddings":
            return serialize_ollama_res(
                self.sdk.embeddings(
                    model=payload["model"],
                    prompt=payload.get("input", ""),
                    options=payload.get("options"),
                    keep_alive=payload.get("keep_alive"),
                )
            )
        if operation == "systemone":
            with self.sdk.Client() as client:
                return client._request_raw("POST", "/v1/systemone", json=payload).json()
        raise ValueError(f"Unsupported request type: {operation}")

    def iter_records(self, operation, payload):
        if operation not in ("generate", "chat"):
            raise ValueError(f"Unsupported streaming request type: {operation}")
        for record in getattr(self.sdk, operation)(**payload):
            yield serialize_ollama_res(record)


class OllamaExecutor:
    """Bounded async HTTP executor for externally running Supabase workers.

    Caller controls total deadlines/leases using task cancellation. A long model
    load has no incidental HTTP read timeout; connection setup stays bounded.
    """

    def __init__(
        self,
        host,
        *,
        client=None,
        metadata_client=None,
        metadata_seconds=5,
        max_output_bytes=RESULT_BYTES,
        max_record_bytes=RECORD_BYTES,
    ):
        if not 0 < metadata_seconds <= 10:
            raise ValueError("invalid_metadata_timeout")
        self.client = client or httpx.AsyncClient(
            base_url=host.rstrip("/"),
            timeout=httpx.Timeout(None, connect=10, pool=10),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=8),
        )
        self.owns_client = client is None
        # Long inference reads must not occupy the control-plane pool. Injected
        # clients remain caller-owned, including when shared by fixture transports.
        self.metadata_client = (
            metadata_client
            or client
            or httpx.AsyncClient(
                base_url=host.rstrip("/"),
                timeout=httpx.Timeout(metadata_seconds),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            )
        )
        self.owns_metadata_client = metadata_client is None and client is None
        self.metadata_seconds = metadata_seconds
        self.max_output_bytes = max_output_bytes
        self.max_record_bytes = max_record_bytes

    async def close(self):
        if self.owns_client:
            await self.client.aclose()
        if self.owns_metadata_client:
            await self.metadata_client.aclose()

    @staticmethod
    def upstream_payload(operation, payload):
        if operation == "generate":
            return GenerateRequest(**payload).model_dump(mode="json", exclude_none=True)
        if operation == "chat":
            return ChatRequest(**payload).model_dump(mode="json", exclude_none=True)
        if operation == "embeddings":
            return EmbeddingsRequest(
                model=payload["model"],
                prompt=payload.get("input", ""),
                options=payload.get("options"),
                keep_alive=payload.get("keep_alive"),
            ).model_dump(mode="json", exclude_none=True)
        if operation == "systemone":
            return payload
        raise ValueError("unsupported_operation")

    @staticmethod
    def normalized_record(operation, record):
        if not isinstance(record, dict):
            raise ValueError("invalid_upstream_response")
        if record.get("error"):
            raise ollama.ResponseError(str(record["error"]))
        if operation == "generate":
            return ollama.GenerateResponse(**record).model_dump(mode="json")
        if operation == "chat":
            return ollama.ChatResponse(**record).model_dump(mode="json")
        if operation == "embeddings":
            return ollama.EmbeddingsResponse(**record).model_dump(mode="json")
        return record

    async def read_body(self, response, limit):
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > limit:
                raise OutputTooLargeError("upstream response exceeds byte budget")
            body.extend(chunk)
        if response.is_error:
            try:
                message = json.loads(body).get("error", "upstream_error")
            except (ValueError, AttributeError):
                message = "upstream_error"
            raise ollama.ResponseError(str(message), response.status_code)
        return bytes(body)

    async def complete(self, operation, payload):
        path = next(
            (path for path, name in INFERENCE_PATHS.items() if name == operation), None
        )
        if path is None:
            raise ValueError("unsupported_operation")
        if operation in ("generate", "chat"):
            payload = {**payload, "stream": False}
        async with self.client.stream(
            "POST", path, json=self.upstream_payload(operation, payload)
        ) as response:
            body = await self.read_body(response, self.max_output_bytes)
        result = self.normalized_record(operation, json.loads(body))
        if operation in ("generate", "chat") and result.get("done") is not True:
            raise IncompleteOutputError(
                "upstream JSON omitted the terminal done marker"
            )
        return result

    async def iter_records(self, operation, payload) -> AsyncIterator[dict]:
        if operation not in ("generate", "chat"):
            raise ValueError("unsupported_stream_operation")
        payload = {**payload, "stream": True}
        total = 0
        pending = bytearray()
        async with self.client.stream(
            "POST", "/api/" + operation, json=self.upstream_payload(operation, payload)
        ) as response:
            if response.is_error:
                await self.read_body(response, self.max_record_bytes)
            # A fixed HTTPX chunk_size waits to fill that many bytes, delaying
            # small model records until EOF. Consume available network bytes.
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self.max_output_bytes:
                    raise OutputTooLargeError("upstream stream exceeds byte budget")
                for offset in range(0, len(chunk), 64 * 1024):
                    pending.extend(chunk[offset : offset + 64 * 1024])
                    while (boundary := pending.find(b"\n")) >= 0:
                        if boundary > self.max_record_bytes:
                            raise OutputTooLargeError(
                                "upstream record exceeds byte budget"
                            )
                        line = bytes(pending[:boundary])
                        del pending[: boundary + 1]
                        if not line.strip():
                            continue
                        record = self.normalized_record(operation, json.loads(line))
                        yield record
                        if record.get("done") is True:
                            return
                    if len(pending) > self.max_record_bytes:
                        raise OutputTooLargeError("upstream record exceeds byte budget")
            if pending.strip():
                record = self.normalized_record(operation, json.loads(pending))
                yield record
                if record.get("done") is True:
                    return
        raise IncompleteOutputError(
            "upstream stream ended without a terminal done marker"
        )

    async def metadata(self, operation, model=None):
        if operation not in ("tags", "ps", "version", "show"):
            raise ValueError("unsupported_metadata_operation")
        async with asyncio.timeout(self.metadata_seconds):
            async with self.metadata_client.stream(
                "POST" if operation == "show" else "GET",
                "/api/" + operation,
                **({"json": {"model": model}} if operation == "show" else {}),
            ) as response:
                value = json.loads(
                    await self.read_body(response, self.max_record_bytes)
                )
                if not isinstance(value, dict):
                    raise ValueError("invalid_upstream_metadata")
                return value
