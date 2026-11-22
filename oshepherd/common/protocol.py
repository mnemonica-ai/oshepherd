"""Backend-neutral request normalization and v1 transport limits."""

import json

from oshepherd.api.chat.models import ChatRequestPayload
from oshepherd.api.embeddings.models import EmbeddingsPayload
from oshepherd.api.generate.models import GenerateRequestPayload
from oshepherd.api.show.models import ShowRequest
from oshepherd.api.systemone.models import SystemOneRequestPayload

PROTOCOL_VERSION = 1
REQUEST_BYTES = 1024 * 1024
RPC_RESPONSE_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
RECORD_BYTES = 1024 * 1024
PAGE_BYTES = 256 * 1024
RESULT_BYTES = 32 * 1024 * 1024
MODELS = {
    "generate": GenerateRequestPayload,
    "chat": ChatRequestPayload,
    "embeddings": EmbeddingsPayload,
    "systemone": SystemOneRequestPayload,
    "show": ShowRequest,
}
INFERENCE_PATHS = {
    "/api/generate": "generate",
    "/api/chat": "chat",
    "/api/embeddings": "embeddings",
    "/v1/systemone": "systemone",
}


def normalize_request(operation: str, payload: dict, *, asynchronous=False) -> dict:
    if operation not in MODELS:
        raise ValueError("unsupported_operation")
    normalized = MODELS[operation].model_validate(payload).model_dump(mode="json")
    if asynchronous and operation not in INFERENCE_PATHS.values():
        raise ValueError("unsupported_operation")
    if (
        asynchronous
        and operation in ("generate", "chat")
        and normalized["stream"] is True
    ):
        raise ValueError("async_stream_not_supported")
    return {"type": operation, "payload": normalized}


def record_bytes(record: dict) -> bytes:
    # Preserve the characterized Redis/SDK serialization on both new gateways.
    return (json.dumps(record) + "\n").encode("utf-8")


def utf8_fragments(data: bytes, limit=CHUNK_BYTES):
    """Split valid UTF-8 without changing any bytes or splitting a codepoint."""
    if limit < 4:
        raise ValueError("UTF-8 fragment limit must be at least four bytes")
    data.decode("utf-8")
    start = 0
    while start < len(data):
        end = min(start + limit, len(data))
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        yield data[start:end]
        start = end
