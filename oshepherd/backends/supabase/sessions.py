"""Single-use worker Auth exchange and atomic, private refresh-token storage."""

import time
import asyncio
import json
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, SecretStr

from oshepherd.backends.supabase.client import RpcError, bounded_response
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)


class WorkerSession(BaseModel):
    version: int = 1
    supabase_url: str
    user_id: UUID
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    expires_at: int

    def save(self, filename):
        data = self.model_dump(mode="json")
        data.update(
            access_token=self.access_token.get_secret_value(),
            refresh_token=self.refresh_token.get_secret_value(),
        )
        write_private_json(filename, data)

    @classmethod
    def load(cls, filename, *, supabase_url):
        session = cls.model_validate(read_private_json(filename))
        if session.version != 1 or session.supabase_url != supabase_url.rstrip("/"):
            raise ValueError("worker session belongs to another project or version")
        return session


class WorkerAuth:
    def __init__(self, url, publishable_key, *, client=None):
        self.url = url.rstrip("/")
        self.key = publishable_key
        self.client = client or httpx.AsyncClient(timeout=10)
        self.owns_client = client is None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def exchange(self, path, payload):
        # Auth verify/refresh must not be automatically replayed: an ambiguous
        # one-time-token exchange needs operator recovery, not duplicate signup.
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(
                    "POST",
                    self.url + "/auth/v1/" + path,
                    headers={"apikey": self.key},
                    json=payload,
                ) as response:
                    if response.is_error:
                        raise RpcError("worker_auth_failed", response.status_code)
                    data = json.loads(await bounded_response(response, 65536))
            return WorkerSession(
                supabase_url=self.url,
                user_id=data["user"]["id"],
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=(
                    data["expires_at"]
                    if "expires_at" in data
                    else int(time.time()) + data["expires_in"]
                ),
            )
        except (httpx.TransportError, TimeoutError):
            raise RpcError("worker_auth_ambiguous") from None
        except (ValueError, KeyError, TypeError):
            raise RpcError("invalid_auth_response") from None

    async def enroll(self, token_hash):
        return await self.exchange(
            "verify", {"token_hash": token_hash, "type": "email"}
        )

    async def refresh(self, session):
        return await self.exchange(
            "token?grant_type=refresh_token",
            {"refresh_token": session.refresh_token.get_secret_value()},
        )
