"""Resumable operator bootstrap and administration, without worker secrets."""

import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from .client import RpcClient, RpcError, bounded_response
from .config import (
    SupabaseConnectionConfig,
    SupabaseGatewayConfig,
    SupabaseProjectConfig,
)
from .private_files import (
    private_directory,
    read_private_json,
    write_private,
    write_private_json,
)
from .releases import Database, OperatorError, migration_path, release_assets


class OperatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = 1
    project_ref: str
    supabase_url: str
    publishable_key: SecretStr = Field(repr=False)
    secret_key: SecretStr = Field(repr=False)
    database_url: SecretStr | None = Field(default=None, repr=False)
    profile: Literal["edge_free", "vercel_hobby"] = "edge_free"
    gateway_url: str | None = None
    exposed_schemas: list[str] | None = None

    @field_validator("exposed_schemas")
    @classmethod
    def schemas(cls, value):
        if value is not None and (
            not value
            or len(value) > 64
            or any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name) for name in value
            )
            or len(set(value)) != len(value)
        ):
            raise ValueError("Invalid existing API schema list")
        return value

    @field_validator("supabase_url")
    @classmethod
    def origin(cls, value):
        return SupabaseProjectConfig.validate_url(value)

    @field_validator("secret_key")
    @classmethod
    def admin_key(cls, value):
        return SupabaseGatewayConfig.administrative_key(value)

    @field_validator("publishable_key")
    @classmethod
    def public_key(cls, value):
        return SupabaseConnectionConfig.reject_administrative_key(value)

    @model_validator(mode="after")
    def target(self):
        if self.project_ref == "local":
            if self.supabase_url != "http://127.0.0.1:55321":
                raise ValueError(
                    "Local installation requires the isolated development stack"
                )
        elif (
            not re.fullmatch(r"[a-z]{20}", self.project_ref)
            or self.supabase_url != f"https://{self.project_ref}.supabase.co"
        ):
            raise ValueError("Project reference and origin must match")
        if self.gateway_url:
            parsed = urlsplit(self.gateway_url)
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not parsed.hostname
            ):
                raise ValueError("Invalid gateway URL")
            if parsed.scheme != "https" and not (
                self.project_ref == "local"
                and parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
            ):
                raise ValueError("Gateway requires HTTPS")
            self.gateway_url = self.gateway_url.rstrip("/")
        return self

    @classmethod
    def load(cls, filename):
        return cls.model_validate(read_private_json(filename))


class Rotation(BaseModel):
    old_key_id: UUID
    key_id: UUID = Field(default_factory=uuid4)
    raw_key: SecretStr = Field(
        default_factory=lambda: SecretStr("osk_" + secrets.token_urlsafe(32)),
        repr=False,
    )
    create_operation: UUID = Field(default_factory=uuid4)
    revoke_operation: UUID = Field(default_factory=uuid4)


class Enrollment(BaseModel):
    capacity: int = Field(ge=1, le=64)
    auth_subject: UUID | None = None
    worker_id: UUID | None = None
    operation_id: UUID = Field(default_factory=uuid4)
    revoke_operation: UUID = Field(default_factory=uuid4)
    issuing: bool = False
    revocation_requested: bool = False


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = 1
    project_ref: str
    supabase_url: str
    profile: Literal["edge_free", "vercel_hobby"]
    owner_subject: UUID
    cluster_id: UUID = Field(default_factory=uuid4)
    key_id: UUID = Field(default_factory=uuid4)
    application_key: SecretStr = Field(
        default_factory=lambda: SecretStr("osk_" + secrets.token_urlsafe(32)),
        repr=False,
    )
    bootstrap_operation: UUID = Field(default_factory=uuid4)
    key_operation: UUID = Field(default_factory=uuid4)
    disable_operation: UUID = Field(default_factory=uuid4)
    stage: Literal[
        "initialized",
        "schema_ready",
        "services_ready",
        "ownership_ready",
        "configured",
        "disabled",
    ] = "initialized"
    workers: dict[str, Enrollment] = Field(default_factory=dict)
    rotation: Rotation | None = None

    def save(self, directory):
        data = self.model_dump(mode="json")
        data["application_key"] = self.application_key.get_secret_value()
        if self.rotation:
            data["rotation"]["raw_key"] = self.rotation.raw_key.get_secret_value()
        write_private_json(Path(directory) / "installation.json", data)

    @classmethod
    def load(cls, directory, config):
        state = cls.model_validate(
            read_private_json(Path(directory) / "installation.json")
        )
        if (state.project_ref, state.supabase_url, state.profile) != (
            config.project_ref,
            config.supabase_url,
            config.profile,
        ):
            raise OperatorError("installation_target_mismatch")
        return state


@contextmanager
def installation_lock(directory):
    directory = private_directory(directory)
    descriptor = os.open(
        directory / "operator.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperatorError("installation_busy") from None
        yield directory
    finally:
        os.close(descriptor)


class Operator:
    def __init__(self, config, directory, *, client=None):
        self.config, self.directory = config, Path(directory).absolute()
        self.client = client or httpx.AsyncClient(timeout=10)
        self.owns_client = client is None
        self.rpc = RpcClient(
            config.supabase_url,
            config.secret_key.get_secret_value(),
            client=self.client,
        )

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def auth_request(self, method, path, **kwargs):
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(
                    method,
                    self.config.supabase_url + "/auth/v1/admin/" + path,
                    headers=self.rpc.headers(),
                    **kwargs,
                ) as response:
                    if response.is_error:
                        raise RpcError("operator_auth_failed", response.status_code)
                    return json.loads(await bounded_response(response, 1024 * 1024))
        except (httpx.HTTPError, TimeoutError):
            raise OperatorError("operator_auth_ambiguous") from None
        except (ValueError, KeyError, TypeError):
            raise OperatorError("invalid_operator_auth_response") from None

    async def ensure_subject(self, state, name, enrollment):
        email = f"oshepherd-{state.cluster_id}-{name}@enrollment.invalid"
        if enrollment.auth_subject:
            user = await self.auth_request(
                "GET", "users/" + str(enrollment.auth_subject)
            )
            if user.get("email") != email:
                raise OperatorError("worker_identity_mismatch")
            return email
        # A deterministic email reconciles lost user-creation acknowledgements.
        for page in range(1, 101):
            result = await self.auth_request(
                "GET", "users", params={"page": page, "per_page": 100}
            )
            users = result.get("users")
            if not isinstance(users, list):
                raise OperatorError("invalid_operator_auth_response")
            matches = [user for user in users if user.get("email") == email]
            if len(matches) > 1:
                raise OperatorError("worker_identity_conflict")
            if matches:
                enrollment.auth_subject = UUID(matches[0]["id"])
                break
            if len(users) < 100:
                user = await self.auth_request(
                    "POST", "users", json={"email": email, "email_confirm": True}
                )
                enrollment.auth_subject = UUID(user["id"])
                break
        else:
            raise OperatorError("worker_identity_scan_limit")
        state.save(self.directory)
        return email

    def export_configuration(self, state):
        config = self.config
        gateway = config.gateway_url or (
            config.supabase_url + "/functions/v1/ollama"
            if config.profile == "edge_free"
            else None
        )
        write_private_json(
            self.directory / "client.json",
            {
                "v": 1,
                "gateway_url": gateway,
                "application_key": state.application_key.get_secret_value(),
            },
        )
        values = {
            "OSHEPHERD_BACKEND": "supabase",
            "SUPABASE_URL": config.supabase_url,
            "SUPABASE_SECRET_KEY": config.secret_key.get_secret_value(),
            "SUPABASE_SCHEMA": "oshepherd_api",
            "OSHEPHERD_CLUSTER_ID": str(state.cluster_id),
            "OSHEPHERD_SYNC_SECONDS": "135",
            "OSHEPHERD_GATEWAY_PROFILE": (
                "vercel" if config.profile == "vercel_hobby" else "persistent"
            ),
            "OSHEPHERD_RESPONSE_BYTES": (
                "4000000" if config.profile == "vercel_hobby" else "1048576"
            ),
        }
        write_private(
            self.directory / "gateway.env",
            "".join(f"{name}={json.dumps(value)}\n" for name, value in values.items()),
        )
        edge = {
            "OSHEPHERD_CLUSTER_ID": str(state.cluster_id),
            "OSHEPHERD_SCHEMA": "oshepherd_api",
            "OSHEPHERD_SUPABASE_URL": config.supabase_url,
            "OSHEPHERD_GATEWAY_KEY": config.secret_key.get_secret_value(),
            "OSHEPHERD_PUBLIC_URL": config.supabase_url,
        }
        write_private(
            self.directory / "edge.env",
            "".join(f"{name}={json.dumps(value)}\n" for name, value in edge.items()),
        )

    async def install(self, owner_subject, *, database=None):
        with installation_lock(self.directory):
            if (self.directory / "installation.json").exists():
                state = Installation.load(self.directory, self.config)
                if state.owner_subject != owner_subject:
                    raise OperatorError("installation_owner_mismatch")
                if state.stage == "disabled":
                    raise OperatorError("installation_disabled")
                if state.rotation is not None:
                    raise OperatorError("pending_key_rotation_use_rotate_key")
            else:
                state = Installation(
                    project_ref=self.config.project_ref,
                    supabase_url=self.config.supabase_url,
                    profile=self.config.profile,
                    owner_subject=owner_subject,
                )
                state.save(self.directory)
            database = database or Database(self.config)
            await asyncio.to_thread(database.migrate)
            state.stage = "schema_ready"
            state.save(self.directory)
            await asyncio.to_thread(database.schedules)
            state.stage = "services_ready"
            state.save(self.directory)
            # Verify the explicit owner's existing Auth identity before binding.
            owner = await self.auth_request("GET", "users/" + str(owner_subject))
            if UUID(owner["id"]) != owner_subject:
                raise OperatorError("installation_owner_mismatch")
            await self.rpc.rpc(
                "bootstrap_cluster",
                {
                    "cluster_id": str(state.cluster_id),
                    "owner_subject": str(owner_subject),
                    "operation_id": str(state.bootstrap_operation),
                },
            )
            await self.rpc.rpc(
                "create_api_key",
                {
                    "cluster_id": str(state.cluster_id),
                    "key_id": str(state.key_id),
                    "raw_key": state.application_key.get_secret_value(),
                    "operation_id": str(state.key_operation),
                },
            )
            info = await self.rpc.rpc(
                "installation_info", {"cluster_id": str(state.cluster_id)}
            )
            if not info["enabled"] or info["owner_subject"] != str(owner_subject):
                raise OperatorError("installation_disabled_or_conflicting")
            state.stage = "ownership_ready"
            state.save(self.directory)
            self.export_configuration(state)
            state.stage = "configured"
            state.save(self.directory)
            return {
                "cluster_id": str(state.cluster_id),
                "stage": state.stage,
                "profile": state.profile,
                "hosted_qualified": False,
                "gateway_deployed": False,
            }

    async def enroll(self, name, capacity, *, renew=False):
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name)
            or type(capacity) is not int
            or not 1 <= capacity <= 64
        ):
            raise OperatorError("invalid_worker_enrollment")
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            if state.stage != "configured":
                raise OperatorError("installation_not_configured")
            enrollment = state.workers.setdefault(name, Enrollment(capacity=capacity))
            if enrollment.revocation_requested:
                raise OperatorError("worker_revoked_use_new_name")
            if len(state.workers) > 64:
                raise OperatorError("worker_enrollment_limit")
            if enrollment.capacity != capacity:
                raise OperatorError("worker_capacity_conflict")
            state.save(self.directory)
            email = await self.ensure_subject(state, name, enrollment)
            result = await self.rpc.rpc(
                "enroll_worker",
                {
                    "cluster_id": str(state.cluster_id),
                    "auth_subject": str(enrollment.auth_subject),
                    "capacity": capacity,
                    "operation_id": str(enrollment.operation_id),
                },
            )
            enrollment.worker_id = UUID(result["worker_id"])
            challenge = self.directory / (name + ".enrollment.json")
            if renew or not challenge.exists():
                if enrollment.issuing and not renew:
                    raise OperatorError("enrollment_issuance_ambiguous_use_renew")
                enrollment.issuing = True
                state.save(self.directory)
                link = await self.auth_request(
                    "POST", "generate_link", json={"type": "magiclink", "email": email}
                )
                write_private_json(
                    challenge,
                    {
                        "v": 1,
                        "supabase_url": self.config.supabase_url,
                        "token_hash": link["hashed_token"],
                    },
                )
            else:
                from .startup import read_enrollment

                read_enrollment(challenge, supabase_url=self.config.supabase_url)
            enrollment.issuing = False
            state.save(self.directory)
            values = {
                "SUPABASE_URL": self.config.supabase_url,
                "SUPABASE_PUBLISHABLE_KEY": self.config.publishable_key.get_secret_value(),
                "SUPABASE_SCHEMA": "oshepherd_api",
                "WORKER_SESSION_FILE": str(
                    self.directory / (name + ".worker-session.json")
                ),
                "CONCURRENCY": str(capacity),
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            }
            write_private(
                self.directory / (name + ".worker.env"),
                "".join(
                    f"{key}={json.dumps(value)}\n" for key, value in values.items()
                ),
            )
            return {
                "worker_id": str(enrollment.worker_id),
                "enrollment_file": str(challenge),
                "worker_env_file": str(self.directory / (name + ".worker.env")),
            }

    async def doctor(self):
        state = Installation.load(self.directory, self.config)
        _, manifest = release_assets()
        info = await self.rpc.rpc(
            "installation_info", {"cluster_id": str(state.cluster_id)}
        )
        expected = [
            {"version": entry["version"], "sha256": entry["content_sha256"]}
            for entry in migration_path(manifest, info["migrations"])
        ]
        matches = info["migrations"] == expected and info["schema_matches"]
        models = None
        if info["enabled"]:
            try:
                inventory = await self.rpc.rpc(
                    "read_metadata",
                    {
                        "cluster_id": str(state.cluster_id),
                        "key": state.application_key.get_secret_value(),
                        "operation": "tags",
                        "model": None,
                    },
                )
                models = len(inventory["response"]["models"])
            except (RpcError, KeyError, TypeError):
                pass
        gateway = self.config.gateway_url or (
            self.config.supabase_url + "/functions/v1/ollama"
            if self.config.profile == "edge_free"
            else None
        )
        available = False
        if gateway:
            try:
                async with asyncio.timeout(5):
                    async with self.client.stream(
                        "GET",
                        gateway + "/health",
                        headers={
                            "Authorization": "Bearer "
                            + state.application_key.get_secret_value()
                        },
                    ) as response:
                        body = await bounded_response(response, 4096)
                        available = response.status_code == 200 and json.loads(
                            body
                        ) == {"status": 200}
            except (httpx.HTTPError, TimeoutError, ValueError, RpcError):
                pass
        return {
            "project_ref": self.config.project_ref,
            "profile": state.profile,
            "schema_matches": bool(matches),
            "enabled": info["enabled"],
            "online_workers": info["online_workers"],
            "models_available": models,
            "active_jobs": info["active_jobs"],
            "gateway_available": available,
            "inference_verified": False,
            "hosted_qualified": False,
        }

    async def rotate_key(self):
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            if state.stage != "configured":
                raise OperatorError("installation_not_configured")
            if state.rotation is None:
                state.rotation = Rotation(old_key_id=state.key_id)
                state.save(self.directory)
            rotation = state.rotation
            await self.rpc.rpc(
                "create_api_key",
                {
                    "cluster_id": str(state.cluster_id),
                    "key_id": str(rotation.key_id),
                    "raw_key": rotation.raw_key.get_secret_value(),
                    "operation_id": str(rotation.create_operation),
                },
            )
            await self.rpc.rpc(
                "backend_info",
                {
                    "cluster_id": str(state.cluster_id),
                    "key": rotation.raw_key.get_secret_value(),
                },
            )
            await self.rpc.rpc(
                "revoke_api_key",
                {
                    "cluster_id": str(state.cluster_id),
                    "key_id": str(rotation.old_key_id),
                    "operation_id": str(rotation.revoke_operation),
                },
            )
            state.key_id, state.application_key, state.key_operation = (
                rotation.key_id,
                rotation.raw_key,
                rotation.create_operation,
            )
            state.save(self.directory)
            self.export_configuration(state)
            state.rotation = None
            state.save(self.directory)
            return {
                "key_id": str(state.key_id),
                "old_key_revoked": True,
                "client_file": str(self.directory / "client.json"),
            }

    async def disable(self):
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            result = await self.rpc.rpc(
                "disable_cluster",
                {
                    "cluster_id": str(state.cluster_id),
                    "operation_id": str(state.disable_operation),
                },
            )
            state.stage = "disabled"
            state.save(self.directory)
            # Shared schema, retained results and cleanup schedules remain owned.
            return result | {"data_preserved": True}

    async def revoke_worker(self, name):
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            enrollment = state.workers.get(name)
            if enrollment is None or enrollment.worker_id is None:
                raise OperatorError("unknown_worker")
            enrollment.revocation_requested = True
            state.save(self.directory)
            return await self.rpc.rpc(
                "revoke_worker",
                {
                    "cluster_id": str(state.cluster_id),
                    "worker_id": str(enrollment.worker_id),
                    "operation_id": str(enrollment.revoke_operation),
                },
            )

    async def verify_inference(self, model):
        state = Installation.load(self.directory, self.config)
        readiness = await self.doctor()
        if (
            not readiness["schema_matches"]
            or not readiness["enabled"]
            or not readiness["gateway_available"]
            or not readiness["online_workers"]
        ):
            raise OperatorError("inference_not_ready")
        gateway = (
            self.config.gateway_url or self.config.supabase_url + "/functions/v1/ollama"
        )
        try:
            async with asyncio.timeout(135):
                async with self.client.stream(
                    "POST",
                    gateway + "/api/generate",
                    headers={
                        "Authorization": "Bearer "
                        + state.application_key.get_secret_value()
                    },
                    json={
                        "model": model,
                        "prompt": "Reply with one short word.",
                        "stream": False,
                        "options": {"num_predict": 8, "num_ctx": 512},
                    },
                    timeout=httpx.Timeout(None, connect=10, pool=10),
                ) as response:
                    body = await bounded_response(response, 1048576)
                    if response.status_code != 200:
                        raise OperatorError("inference_failed")
                    result = json.loads(body)
                    if not isinstance(result, dict) or result.get("done") is not True:
                        raise OperatorError("inference_incomplete")
                    return {
                        "inference_verified": True,
                        "model": model,
                        "response_bytes": len(body),
                        "eval_count": result.get("eval_count"),
                        "hosted_qualified": False,
                    }
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise OperatorError("inference_failed") from None
