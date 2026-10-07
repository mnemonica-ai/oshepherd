"""HTTP-only, versioned RPC client. No SDK-global authentication state."""

import asyncio
import copy
import random
import re
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import json

import httpx

from oshepherd.common.protocol import PROTOCOL_VERSION, RPC_RESPONSE_BYTES

RPCS = {
    "bootstrap_cluster",
    "create_api_key",
    "enroll_worker",
    "revoke_worker",
    "register_worker",
    "update_models",
    "heartbeat_and_claim",
    "submit_job",
    "append_output",
    "finish_job",
    "fail_attempt",
    "read_updates",
    "cancel_job",
    "read_metadata",
    "backend_info",
    "reap_jobs",
    "purge_expired",
    "maintenance_tick",
    "complete_maintenance",
}


class RpcError(RuntimeError):
    def __init__(self, code, status=503):
        self.code = (
            code
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(code))
            else "backend_error"
        )
        self.status = status
        super().__init__(self.code)


class RpcClient:
    def __init__(
        self,
        url,
        key,
        *,
        access_token=None,
        schema="oshepherd_api",
        client=None,
        sleep=asyncio.sleep,
    ):
        self.url = url.rstrip("/")
        self.key = key
        self.access_token = access_token
        self.schema = schema
        self.client = client or httpx.AsyncClient(timeout=10)
        self.owns_client = client is None
        self.sleep = sleep

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    def headers(self):
        headers = {
            "apikey": self.key,
            "Content-Profile": self.schema,
            "Accept-Profile": self.schema,
        }
        token = self.access_token or (self.key if self.key.startswith("eyJ") else None)
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    async def rpc(self, name, request, *, attempts=4):
        if name not in RPCS:
            raise ValueError("unsupported_rpc")
        if type(attempts) is not int or not 1 <= attempts <= 4:
            raise ValueError("invalid_retry_limit")
        # Generate once outside the transport retry loop. Callers replaying a
        # process-level ambiguous outcome must supply their saved operation ID.
        envelope = copy.deepcopy(request)
        envelope.setdefault("v", PROTOCOL_VERSION)
        envelope.setdefault("operation_id", str(uuid.uuid4()))
        for attempt in range(attempts):
            retry_after = None
            try:
                # HTTPX's ordinary .post buffers eagerly. Bound the complete
                # read, including a peer that continually sends tiny fragments.
                async with asyncio.timeout(10):
                    async with self.client.stream(
                        "POST",
                        self.url + "/rest/v1/rpc/" + name,
                        headers=self.headers(),
                        json={"p_request": envelope},
                    ) as response:
                        content = await bounded_response(response, RPC_RESPONSE_BYTES)
                        status = response.status_code
                        retry_after = bounded_retry_after(
                            response.headers.get("Retry-After")
                        )
            except (httpx.TransportError, TimeoutError) as error:
                if attempt + 1 == attempts:
                    raise RpcError("backend_unavailable") from error
            else:
                if status not in (429, 502, 503, 504) or attempt + 1 == attempts:
                    if status >= 400:
                        try:
                            code = json.loads(content).get("message", "backend_error")
                        except (ValueError, AttributeError):
                            code = "backend_error"
                        raise RpcError(code, status)
                    try:
                        result = json.loads(content)
                    except ValueError as error:
                        raise RpcError("invalid_rpc_response") from error
                    if (
                        not isinstance(result, dict)
                        or type(result.get("v")) is not int
                        or result.get("v") != PROTOCOL_VERSION
                        or not isinstance(result.get("data"), dict)
                    ):
                        raise RpcError("protocol_mismatch")
                    return result["data"]
            # Finite backoff. No inference is retried here: only identical RPCs.
            await self.sleep(
                retry_after
                if retry_after is not None
                else min(0.2 * 2**attempt, 2) * random.uniform(0.8, 1.2)
            )
        raise RpcError("backend_unavailable")


async def bounded_response(response, limit):
    content = bytearray()
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > limit:
            raise RpcError("response_too_large")
        content.extend(chunk)
    return bytes(content)


def bounded_retry_after(value):
    """Honor either HTTP form without accepting an unbounded server delay."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (
                parsedate_to_datetime(value) - datetime.now(timezone.utc)
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(2.0, max(0.0, seconds)) if math.isfinite(seconds) else None
