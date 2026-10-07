from pathlib import Path
import base64
import json
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from typing import Literal
from uuid import UUID


class SupabaseProjectConfig(BaseModel):
    SUPABASE_URL: str
    SUPABASE_SCHEMA: str = "oshepherd_api"
    LOGLEVEL: str = "info"

    @field_validator("SUPABASE_URL")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("SUPABASE_URL must be an HTTP(S) project origin")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
            "host.docker.internal",
        ):
            raise ValueError("HTTP is allowed only for local development")
        return value.rstrip("/")

    @field_validator("SUPABASE_SCHEMA")
    @classmethod
    def validate_schema(cls, value):
        if value not in ("oshepherd_api", "oshepherd_spike_api"):
            raise ValueError("unsupported RPC schema")
        return value


class SupabaseConnectionConfig(SupabaseProjectConfig):
    SUPABASE_PUBLISHABLE_KEY: SecretStr

    @field_validator("SUPABASE_PUBLISHABLE_KEY")
    @classmethod
    def reject_administrative_key(cls, value):
        key = value.get_secret_value()
        if not key or key.startswith("sb_secret_"):
            raise ValueError(
                "workers require a publishable key, never an administrative key"
            )
        if key.startswith("eyJ"):
            if legacy_role(key) != "anon":
                raise ValueError("workers require the legacy anon key")
        return value


def legacy_role(key):
    try:
        segment = key.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        )
        return claims.get("role")
    except (ValueError, IndexError, AttributeError):
        raise ValueError("invalid legacy project key") from None


class SupabaseGatewayConfig(SupabaseProjectConfig):
    SUPABASE_SECRET_KEY: SecretStr
    OSHEPHERD_CLUSTER_ID: UUID
    OSHEPHERD_GATEWAY_PROFILE: Literal["persistent", "vercel"] = "persistent"
    # Experimental SQL currently enforces this same conservative maximum.
    OSHEPHERD_SYNC_SECONDS: float = Field(default=135, gt=0, le=135)
    OSHEPHERD_RESPONSE_BYTES: int = Field(default=1048576, ge=262144, le=33554432)
    OSHEPHERD_READER_STALL_SECONDS: float = Field(default=30, gt=0, le=30)
    OSHEPHERD_ALLOWED_ORIGINS: str = ""
    OSHEPHERD_PUBLIC_URL: str | None = None
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=5001, ge=1, le=65535)
    WORKERS: int = Field(default=1, ge=1)
    UVICORN_ACCESS_LOG: bool = False

    @field_validator("SUPABASE_SECRET_KEY")
    @classmethod
    def administrative_key(cls, value):
        key = value.get_secret_value()
        if not key.startswith("sb_secret_") and not (
            key.startswith("eyJ") and legacy_role(key) == "service_role"
        ):
            raise ValueError("gateway requires a server-only project secret key")
        return value

    @field_validator("OSHEPHERD_PUBLIC_URL")
    @classmethod
    def public_url(cls, value):
        return cls.validate_url(value) if value is not None else None

    @model_validator(mode="after")
    def hosting_budget(self):
        if (
            self.OSHEPHERD_GATEWAY_PROFILE == "vercel"
            and self.OSHEPHERD_RESPONSE_BYTES > 4000000
        ):
            raise ValueError("Vercel response budget must not exceed 4000000 bytes")
        return self


class SupabaseWorkerConfig(SupabaseConnectionConfig):
    WORKER_SESSION_FILE: Path = Path(".oshepherd/worker-session.json")
    OLLAMA_BASE_URL: str | None = None
    OLLAMA_HOST: str | None = None
    CONCURRENCY: int = Field(default=1, ge=1, le=64)
    PREFETCH_MULTIPLIER: Literal[1] = 1
    HEARTBEAT_SECONDS: float = Field(default=10, gt=0, le=10)
    DRAIN_SECONDS: float = Field(default=20, ge=0, le=60)
    SYSTEMONE_ENABLED: bool = False

    @property
    def ollama_url(self):
        value = self.OLLAMA_BASE_URL or self.OLLAMA_HOST or "http://localhost:11434"
        if "://" not in value:
            value = "http://" + value
        return value.rstrip("/")


class SupabaseMaintenanceConfig(SupabaseProjectConfig):
    SUPABASE_SECRET_KEY: SecretStr
    OSHEPHERD_CLUSTER_ID: UUID
    MAINTENANCE_SECONDS: float = Field(default=10, ge=10, le=3600)
    MAINTENANCE_BATCH: int = Field(default=32, ge=1, le=64)

    @field_validator("SUPABASE_SECRET_KEY")
    @classmethod
    def administrative_key(cls, value):
        return SupabaseGatewayConfig.administrative_key(value)
