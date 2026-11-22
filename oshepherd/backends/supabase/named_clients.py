"""Journaled bearer credentials for applications that consume inference."""

import os
from pathlib import Path
import re
import secrets
import stat
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .client import RpcError
from .operator import Installation, installation_lock
from .private_files import output_directory, read_private_json, write_private_json
from .releases import OperatorError
from .sharing import gateway_url


def validate_name(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise OperatorError(
            "Client name must contain 1–64 lowercase letters, digits, hyphens or "
            "underscores and start with a letter or digit"
        )


class ClientRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]
    name: str
    cluster_id: UUID
    gateway_url: str
    key_id: UUID
    create_operation: UUID
    create_confirmed: bool
    revoke_operation: UUID | None
    raw_key: SecretStr | None = Field(repr=False)

    @model_validator(mode="after")
    def validate_key(self):
        if self.raw_key is None:
            if self.revoke_operation is None or not self.create_confirmed:
                raise ValueError("Incomplete revocation receipt")
        elif not re.fullmatch(
            r"osk_[A-Za-z0-9_-]{43}", self.raw_key.get_secret_value()
        ):
            raise ValueError("Invalid application key")
        return self

    def save(self, path):
        data = self.model_dump(mode="json")
        if self.raw_key is not None:
            data["raw_key"] = self.raw_key.get_secret_value()
        write_private_json(path, data)

    def metadata(self):
        if self.raw_key is None:
            status = "revoked"
        elif self.revoke_operation is not None:
            status = "revoking"
        else:
            status = "active" if self.create_confirmed else "creating"
        return {"name": self.name, "key_id": str(self.key_id), "status": status}

    def bundle(self):
        return {
            "v": 1,
            "gateway_url": self.gateway_url,
            "application_key": self.raw_key.get_secret_value(),
        }


def matching_output(path, bundle):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if (
        bundle is not None
        and stat.S_ISREG(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o600
    ):
        try:
            if read_private_json(path) == bundle:
                return True
        except (OSError, ValueError):
            pass
    raise OperatorError("Client output already exists. Choose a new file.")


class NamedClients:
    def __init__(self, operator):
        self.operator = operator
        self.directory = Path(os.path.abspath(operator.directory)) / "clients"
        self.gateway = gateway_url(operator.config)

    def installation(self, *, mutate=False):
        state = Installation.load(self.operator.directory, self.operator.config)
        if mutate and state.stage == "disabled":
            raise OperatorError("installation_disabled")
        if mutate and state.stage != "configured":
            raise OperatorError("installation_not_configured")
        return state

    def load(self, path, state):
        validate_name(path.stem)
        record = ClientRecord.model_validate(read_private_json(path))
        if (
            record.name != path.stem
            or record.cluster_id != state.cluster_id
            or record.gateway_url != self.gateway
            or record.key_id == state.key_id
            or (state.rotation is not None and record.key_id == state.rotation.key_id)
        ):
            raise OperatorError("client_record_target_mismatch")
        return record

    def list(self):
        state = self.installation()
        if self.directory.is_symlink() or (
            self.directory.exists() and not self.directory.is_dir()
        ):
            raise OperatorError("invalid_client_registry")
        return [
            self.load(path, state).metadata()
            for path in sorted(self.directory.glob("*.json"))
        ]

    async def rpc(self, name, request):
        try:
            return await self.operator.rpc.rpc(name, request)
        except RpcError:
            # The RPC peer controls its error text, which could echo a credential.
            raise OperatorError(
                f"Client operation failed during {name}. Retry with the same name."
            ) from None

    async def require_enabled(self, state):
        info = await self.rpc(
            "installation_info", {"cluster_id": str(state.cluster_id)}
        )
        if info.get("cluster_id") != str(state.cluster_id) or info.get(
            "owner_subject"
        ) != str(state.owner_subject):
            raise OperatorError("installation_target_mismatch")
        if info.get("enabled") is not True:
            raise OperatorError("installation_disabled")

    async def confirm_create(self, path, record):
        if record.create_confirmed:
            return
        result = await self.rpc(
            "create_api_key",
            {
                "cluster_id": str(record.cluster_id),
                "key_id": str(record.key_id),
                "raw_key": record.raw_key.get_secret_value(),
                "operation_id": str(record.create_operation),
            },
        )
        if result.get("key_id") != str(record.key_id) or result.get(
            "cluster_id"
        ) != str(record.cluster_id):
            raise OperatorError("invalid_client_create_receipt")
        record.create_confirmed = True
        record.save(path)

    async def create(self, name, output):
        validate_name(name)
        output = Path(os.path.abspath(output))
        registry = tuple(part.casefold() for part in self.directory.parts)
        if tuple(part.casefold() for part in output.parts[: len(registry)]) == registry:
            raise OperatorError("Choose a client output outside the client registry")
        try:
            output_directory(output.parent)
        except (OSError, ValueError):
            raise OperatorError(
                "Choose an existing client output directory that you own and "
                "that only you can write to"
            ) from None
        path = self.directory / (name + ".json")
        with installation_lock(self.operator.directory):
            state = self.installation(mutate=True)
            try:
                record = self.load(path, state)
            except FileNotFoundError:
                record = None
            if record is not None and record.revoke_operation is not None:
                raise OperatorError("Client name is reserved by a revocation")
            published = matching_output(output, record.bundle() if record else None)
            await self.require_enabled(state)
            if record is None:
                record = ClientRecord(
                    v=1,
                    name=name,
                    cluster_id=state.cluster_id,
                    gateway_url=self.gateway,
                    key_id=uuid4(),
                    create_operation=uuid4(),
                    create_confirmed=False,
                    revoke_operation=None,
                    raw_key=SecretStr("osk_" + secrets.token_urlsafe(32)),
                )
                record.save(path)
            await self.confirm_create(path, record)
            bundle = record.bundle()
            info = await self.rpc(
                "backend_info",
                {
                    "cluster_id": str(record.cluster_id),
                    "key": bundle["application_key"],
                },
            )
            if info.get("cluster_id") != str(record.cluster_id):
                raise OperatorError("client_key_target_mismatch")
            if not published:
                try:
                    write_private_json(output, bundle, replace=False)
                except FileExistsError:
                    raise OperatorError(
                        "Client output already exists. Choose a new file."
                    ) from None
            return record.metadata() | {"client_file": str(output)}

    async def revoke(self, name):
        validate_name(name)
        path = self.directory / (name + ".json")
        with installation_lock(self.operator.directory):
            state = self.installation(mutate=True)
            try:
                record = self.load(path, state)
            except FileNotFoundError:
                raise OperatorError("Unknown client name") from None
            if record.raw_key is None:
                return record.metadata()
            await self.require_enabled(state)
            if record.revoke_operation is None:
                record.revoke_operation = uuid4()
                record.save(path)
            # Reconcile a lost create response before revoking the same key ID.
            await self.confirm_create(path, record)
            result = await self.rpc(
                "revoke_api_key",
                {
                    "cluster_id": str(record.cluster_id),
                    "key_id": str(record.key_id),
                    "operation_id": str(record.revoke_operation),
                },
            )
            if (
                result.get("key_id") != str(record.key_id)
                or result.get("revoked") is not True
            ):
                raise OperatorError("invalid_client_revoke_receipt")
            record.raw_key = None
            record.save(path)
            return record.metadata()
