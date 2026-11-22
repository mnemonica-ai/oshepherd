"""Privileged reads/signatures after SQL has authorized the application caller."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import asyncio

import httpx

from .client import RpcError, bounded_response

PATH = re.compile(
    r"(?:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/){3}result\.json"
)


class GatewayResultStorage:
    def __init__(self, rpc):
        self.rpc = rpc
        self.bucket = (
            "oshepherd-spike-results"
            if rpc.schema == "oshepherd_spike_api"
            else "oshepherd-results"
        )

    def path(self, descriptor):
        path = descriptor.get("object_path")
        if not isinstance(path, str) or not PATH.fullmatch(path):
            raise RpcError("invalid_result")
        return self.bucket + "/" + path

    async def request(self, method, path, *, limit, **kwargs):
        try:
            async with asyncio.timeout(10):
                async with self.rpc.client.stream(
                    method,
                    self.rpc.url + "/storage/v1/" + path,
                    headers=self.rpc.headers(),
                    **kwargs,
                ) as response:
                    if response.is_error:
                        raise RpcError("storage_unavailable")
                    return await bounded_response(response, limit)
        except (httpx.TransportError, TimeoutError):
            raise RpcError("storage_unavailable") from None

    async def read(self, descriptor, *, limit):
        size = descriptor.get("size_bytes")
        if type(size) is not int or size < 0:
            raise RpcError("invalid_result")
        if size > limit:
            raise RpcError("result_too_large", 413)
        content = await self.request(
            "GET", "object/" + self.path(descriptor), limit=limit
        )
        if len(content) != size or hashlib.sha256(
            content
        ).hexdigest() != descriptor.get("checksum"):
            raise RpcError("invalid_result")
        return content

    async def sign(self, descriptor, expires_at):
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            seconds = min(
                300, int((expiry - datetime.now(timezone.utc)).total_seconds())
            )
        except (ValueError, AttributeError, TypeError):
            raise RpcError("invalid_result") from None
        if seconds < 1:
            raise RpcError("result_expired", 410)
        path = self.path(descriptor)
        content = await self.request(
            "POST", "object/sign/" + path, limit=65536, json={"expiresIn": seconds}
        )
        try:
            value = json.loads(content)
            url = value["signedURL"]
            if not isinstance(url, str) or not url.startswith(
                "/object/sign/" + path + "?"
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RpcError("invalid_storage_response") from None
        return {
            "url": self.rpc.url + "/storage/v1" + url,
            "size_bytes": descriptor["size_bytes"],
            "sha256": descriptor["checksum"],
            "content_type": descriptor["content_type"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
