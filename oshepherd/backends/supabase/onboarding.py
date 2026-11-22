"""Portable worker invitations and bounded, read-only Ollama discovery."""

import asyncio
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from .client import bounded_response
from .config import SupabaseConnectionConfig, legacy_role
from .private_files import private_directory, read_private_json, write_private_json

INVITATION_BYTES = 16384
CODE_PREFIX = "oshepherd-join-v1."


class OnboardingError(ValueError):
    """A fixed, safe-to-display onboarding error."""


def default_state_dir(kind="worker"):
    if kind not in {"worker", "share"}:
        raise ValueError("invalid_state_kind")
    root = os.environ.get("XDG_STATE_HOME")
    if root and not Path(root).is_absolute():
        raise OnboardingError("XDG_STATE_HOME must be an absolute directory")
    return (
        (Path(root) if root else Path.home() / ".local" / "state") / "oshepherd" / kind
    )


class WorkerInvitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int = 1
    supabase_url: str
    publishable_key: SecretStr = Field(repr=False)
    token_hash: SecretStr = Field(repr=False)
    schema_name: str = Field(default="oshepherd_api", alias="schema")
    capacity: int = Field(default=1, ge=1, le=64)

    @field_validator("v")
    @classmethod
    def version(cls, value):
        if value != 1:
            raise ValueError("unsupported_invitation_version")
        return value

    @field_validator("token_hash")
    @classmethod
    def token(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", value.get_secret_value()):
            raise ValueError("invalid_enrollment_token")
        return value

    @model_validator(mode="after")
    def connection(self):
        if len(self.supabase_url) > 2048 or any(
            ord(c) <= 32 or ord(c) == 127 for c in self.supabase_url
        ):
            raise ValueError("invalid_project_origin")
        if len(self.publishable_key.get_secret_value()) > 4096:
            raise ValueError("invalid_publishable_key")
        config = SupabaseConnectionConfig(
            SUPABASE_URL=self.supabase_url,
            SUPABASE_PUBLISHABLE_KEY=self.publishable_key,
            SUPABASE_SCHEMA=self.schema_name,
        )
        self.supabase_url = config.SUPABASE_URL
        parsed = urlsplit(self.supabase_url)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("invalid_project_port")
        key = self.publishable_key.get_secret_value()
        if key.startswith("eyJ"):
            if legacy_role(key) != "anon":
                raise ValueError("invitation_requires_publishable_key")
            segment = key.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
            )
            hostname = urlsplit(self.supabase_url).hostname
            if (
                hostname.endswith(".supabase.co")
                and claims.get("ref") != hostname.split(".")[0]
            ):
                raise ValueError("invitation_project_key_mismatch")
        elif not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{8,2048}", key):
            raise ValueError("invitation_requires_publishable_key")
        return self

    def private_data(self):
        data = self.model_dump(mode="json", by_alias=True)
        data.update(
            publishable_key=self.publishable_key.get_secret_value(),
            token_hash=self.token_hash.get_secret_value(),
        )
        return data

    def save(self, filename):
        write_private_json(filename, self.private_data())

    def encode(self):
        """Return a bearer secret for private transfer; never routine CLI output."""
        raw = json.dumps(self.private_data(), separators=(",", ":")).encode()
        return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def _wire(cls, data):
        if not isinstance(data, dict) or set(data) != {
            "v",
            "supabase_url",
            "publishable_key",
            "token_hash",
            "schema",
            "capacity",
        }:
            raise ValueError("invalid_invitation_shape")
        return cls.model_validate(data)

    @classmethod
    def load(cls, filename):
        try:
            return cls._wire(read_private_json(filename, limit=INVITATION_BYTES))
        except (OSError, ValueError, TypeError):
            raise OnboardingError(
                "Invalid invitation; use an owned private file with mode 0600"
            ) from None

    @classmethod
    def decode(cls, code):
        try:
            if (
                not isinstance(code, str)
                or len(code) > INVITATION_BYTES * 2
                or not code.startswith(CODE_PREFIX)
            ):
                raise ValueError()
            encoded = code[len(CODE_PREFIX) :]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
                raise ValueError()
            raw = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            if len(raw) > INVITATION_BYTES:
                raise ValueError()
            return cls._wire(json.loads(raw))
        except (ValueError, TypeError):
            raise OnboardingError("Invalid or unsupported invitation code") from None


def export_invitation(config, enrollment_file, capacity, destination):
    """Export an existing operator challenge without provider/admin credentials."""
    from .startup import read_enrollment

    invitation = WorkerInvitation(
        supabase_url=config.supabase_url,
        publishable_key=config.publishable_key,
        token_hash=read_enrollment(enrollment_file, supabase_url=config.supabase_url),
        capacity=capacity,
    )
    # Publication is exclusive even if another process creates the destination
    # while enrollment is in flight. Never replace an operator or worker file.
    destination = Path(destination).absolute()
    directory = private_directory(destination.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".invitation-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(invitation.private_data(), stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise OnboardingError(
                "Invitation output already exists; choose a new private file"
            ) from None
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.unlink(temporary)
    return invitation


def ollama_origin(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 2048
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError()
        if "://" not in value:
            value = "http://" + value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
        return value.rstrip("/")
    except ValueError:
        raise OnboardingError(
            "Ollama URL must be an HTTP(S) origin without credentials"
        ) from None


validate_ollama_url = ollama_origin


@dataclass(frozen=True)
class OllamaDiscovery:
    url: str
    models: tuple[str, ...]
    model: str | None


async def discover_ollama(url="http://127.0.0.1:11434", model=None, *, client=None):
    url = ollama_origin(url)
    owned = client is None
    client = client or httpx.AsyncClient(timeout=5, follow_redirects=False)
    try:
        async with asyncio.timeout(10):
            async with client.stream("GET", url + "/api/tags") as response:
                if response.status_code != 200:
                    raise OnboardingError(
                        "Ollama discovery failed; check the server URL and that Ollama is running"
                    )
                data = json.loads(await bounded_response(response, 262144))
        values = data.get("models") if isinstance(data, dict) else None
        if not isinstance(values, list) or len(values) > 256:
            raise ValueError()
        names = []
        for value in values:
            name = value.get("name") if isinstance(value, dict) else None
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
            ):
                raise ValueError()
            if name not in names:
                names.append(name)
        if not names:
            raise OnboardingError(
                "Ollama has no installed models; install a model separately, then retry"
            )
        if model is not None and model not in names:
            raise OnboardingError(
                "Selected model is not installed on this Ollama server"
            )
        return OllamaDiscovery(
            url, tuple(names), model or (names[0] if len(names) == 1 else None)
        )
    except OnboardingError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise OnboardingError(
            "Cannot reach Ollama; start the server or select its URL with --ollama-url"
        ) from None
    except Exception:
        raise OnboardingError(
            "Ollama returned an invalid or oversized model inventory"
        ) from None
    finally:
        if owned:
            await client.aclose()
