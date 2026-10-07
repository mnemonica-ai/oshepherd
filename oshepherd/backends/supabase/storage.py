"""Immutable attempt-specific uploads using only the current worker identity."""

import asyncio
import hashlib
import json
from urllib.parse import quote
from uuid import UUID

import httpx

from .client import RpcError


class WorkerResultStorage:
    def __init__(self, rpc):
        self.rpc = rpc
        self.bucket = (
            "oshepherd-spike-results"
            if rpc.schema == "oshepherd_spike_api"
            else "oshepherd-results"
        )

    def url(self, path):
        return (
            self.rpc.url
            + "/storage/v1/object/"
            + self.bucket
            + "/"
            + quote(path, safe="/")
        )

    async def verify_existing(self, path, content, *, missing_ok=False):
        digest, size = hashlib.sha256(), 0
        async with self.rpc.client.stream(
            "GET", self.url(path), headers=self.rpc.headers()
        ) as response:
            if response.is_error:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 65536:
                        raise RpcError("invalid_storage_response")
                    body.extend(chunk)
                try:
                    code = str(json.loads(body).get("statusCode"))
                except (ValueError, AttributeError):
                    code = None
                if missing_ok and (response.status_code == 404 or code == "404"):
                    return False
                raise RpcError("storage_unavailable")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > len(content):
                    raise RpcError("result_object_conflict", 409)
                digest.update(chunk)
        if size != len(content) or digest.digest() != hashlib.sha256(content).digest():
            raise RpcError("result_object_conflict", 409)
        return True

    async def upload(self, cluster_id, claim, content):
        if not 1 <= len(content) <= min(33554432, claim["max_output_bytes"]):
            raise RpcError("output_too_large", 413)
        path = (
            "/".join(
                str(UUID(value))
                for value in (cluster_id, claim["job_id"], claim["attempt_id"])
            )
            + "/result.json"
        )
        for attempt in range(4):
            try:
                # Resume an ambiguous upload without resending a large body to
                # an existing immutable object (some proxies reject it early).
                if await self.verify_existing(path, content, missing_ok=True):
                    break
                async with self.rpc.client.stream(
                    "POST",
                    self.url(path),
                    content=content,
                    headers={
                        **self.rpc.headers(),
                        "Content-Type": "application/json",
                        "x-upsert": "false",
                    },
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 65536:
                            raise RpcError("invalid_storage_response")
                        body.extend(chunk)
                    if response.is_success:
                        break
                    duplicate = response.status_code == 409
                    if not duplicate:
                        try:
                            duplicate = str(json.loads(body).get("statusCode")) == "409"
                        except (ValueError, AttributeError):
                            pass
                    if duplicate:
                        await self.verify_existing(path, content)
                        break
                    if response.status_code not in (429, 502, 503, 504):
                        raise RpcError("storage_write_denied", response.status_code)
            except httpx.TransportError:
                pass
            if attempt == 3:
                raise RpcError("storage_unavailable")
            await asyncio.sleep(min(0.2 * 2**attempt, 2))
        return {
            "inline": None,
            "object_path": path,
            "size_bytes": len(content),
            "checksum": hashlib.sha256(content).hexdigest(),
            "content_type": "application/json",
        }
