"""Hobby-only REST deployment adapter with provider ownership verification.

Use an explicit existing project, verified release wheel, Production-only
configuration and a private receipt. A lost deployment acknowledgement is
reconciled by operation metadata rather than creating another deployment.
"""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .client import bounded_response, RpcError
from .operator import Installation, installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError, export_vercel
from .runtime_bundle import materialize_current_wheel


class VercelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str = Field(pattern=r"^team_[A-Za-z0-9]+$")
    project_id: str = Field(pattern=r"^prj_[A-Za-z0-9]+$")
    access_token: SecretStr = Field(repr=False, min_length=1, max_length=8192)

    @classmethod
    def load(cls, filename):
        return cls.model_validate(read_private_json(filename))


class VercelDeployment:
    def __init__(
        self, target, config, directory, *, client=None, runner=subprocess.run
    ):
        if (
            config.profile != "vercel_hobby"
            or not config.gateway_url
            or config.project_ref == "local"
        ):
            raise OperatorError("hosted_vercel_profile_required")
        self.target, self.config = target, config
        self.directory = Path(directory)
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.owns_client = client is None
        self.runner = runner
        self.receipt_file = self.directory / "vercel-deployment.json"

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def request(self, path, *, method="GET", body=None, **params):
        # Only official provider requests. Scope cannot be overridden.
        if method not in ("GET", "DELETE", "POST"):
            raise OperatorError("unsupported_vercel_operation")
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(
                    method,
                    "https://api.vercel.com" + path,
                    params={**params, "teamId": self.target.team_id},
                    headers={
                        "Authorization": "Bearer "
                        + self.target.access_token.get_secret_value()
                    },
                    json=body,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (401, 403):
                        raise OperatorError("vercel_access_denied")
                    if response.status_code == 404:
                        raise OperatorError("vercel_resource_missing")
                    if response.status_code not in (200, 201, 204):
                        raise OperatorError("vercel_provider_failed")
                    content = await bounded_response(response, 1024 * 1024)
                    value = json.loads(content) if content else None
                    if content and not isinstance(value, dict):
                        raise ValueError()
                    return value
        except (httpx.HTTPError, TimeoutError):
            raise OperatorError(
                "vercel_provider_unavailable"
                if method == "GET"
                else (
                    "vercel_removal_outcome_ambiguous"
                    if method == "DELETE"
                    else "vercel_write_outcome_ambiguous"
                )
            ) from None
        except (ValueError, RpcError):
            raise OperatorError("invalid_vercel_response") from None

    async def preflight(self):
        team = await self.request("/v2/teams/" + self.target.team_id)
        project = await self.request("/v9/projects/" + self.target.project_id)
        domains = await self.request(
            "/v9/projects/" + self.target.project_id + "/domains"
        )
        try:
            if team["id"] != self.target.team_id or team["billing"]["plan"] != "hobby":
                raise OperatorError("vercel_hobby_team_required")
            if (
                project["id"] != self.target.project_id
                or project["accountId"] != self.target.team_id
            ):
                raise OperatorError("vercel_project_owner_mismatch")
            if project.get("framework") not in (None, "fastapi"):
                raise OperatorError("vercel_framework_collision")
            gateway = urlsplit(self.config.gateway_url)
            if (
                gateway.path
                or gateway.port not in (None, 443)
                or gateway.hostname
                not in {
                    item["name"]
                    for item in domains["domains"]
                    if item.get("verified") is True
                }
            ):
                raise OperatorError("vercel_gateway_domain_not_owned")
            if not re.fullmatch(r"[a-z0-9-]{1,100}", team["slug"]):
                raise ValueError()
            return team["slug"], project
        except (ValueError, KeyError, TypeError):
            raise OperatorError("invalid_vercel_response") from None

    def values(self, state):
        return {
            "OSHEPHERD_BACKEND": "supabase",
            "SUPABASE_URL": self.config.supabase_url,
            "SUPABASE_SECRET_KEY": self.config.secret_key.get_secret_value(),
            "SUPABASE_SCHEMA": "oshepherd_api",
            "OSHEPHERD_CLUSTER_ID": str(state.cluster_id),
            "OSHEPHERD_SYNC_SECONDS": "135",
            "OSHEPHERD_GATEWAY_PROFILE": "vercel",
            "OSHEPHERD_RESPONSE_BYTES": "4000000",
        }

    async def health(self, state):
        try:
            async with asyncio.timeout(10):
                for authorized in (False, True):
                    headers = (
                        {
                            "Authorization": "Bearer "
                            + state.application_key.get_secret_value()
                        }
                        if authorized
                        else {}
                    )
                    async with self.client.stream(
                        "GET",
                        self.config.gateway_url + "/health",
                        headers=headers,
                        follow_redirects=False,
                    ) as response:
                        body = await bounded_response(response, 4096)
                        if response.status_code != (200 if authorized else 401) or (
                            authorized and json.loads(body) != {"status": 200}
                        ):
                            return False
            return True
        except (httpx.HTTPError, TimeoutError, ValueError, RpcError):
            return False

    async def find_operation(self, receipt):
        found, until = [], None
        for _ in range(10):
            params = {
                "projectId": self.target.project_id,
                "limit": 100,
                "since": receipt["started_at"],
            }
            if until is not None:
                params["until"] = until
            page = await self.request("/v6/deployments", **params)
            try:
                for item in page["deployments"]:
                    if (
                        item.get("meta", {}).get("oshepherd_operation")
                        == receipt["operation_id"]
                    ):
                        found.append(item["uid"])
                until = page.get("pagination", {}).get("next")
                if until is None:
                    break
            except (KeyError, TypeError, AttributeError):
                raise OperatorError("invalid_vercel_response") from None
        if until is not None or len(set(found)) != 1:
            raise OperatorError("vercel_deployment_ambiguous_reconcile_operation")
        return found[0]

    async def deploy(self, wheel=None, *, adopt_existing=False):
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            if state.stage != "configured":
                raise OperatorError("installation_not_configured")
            if wheel is None:
                wheel = await asyncio.to_thread(
                    materialize_current_wheel, self.directory / "runtime-wheel"
                )
            scope, project = await self.preflight()
            values = self.values(state)
            environments = await self.request(
                "/v9/projects/" + self.target.project_id + "/env"
            )
            try:
                selected = [
                    item for item in environments["envs"] if item["key"] in values
                ]
                if any(
                    item["target"] != ["production"]
                    or item["type"] != "encrypted"
                    or item.get("gitBranch")
                    or item.get("customEnvironmentIds")
                    for item in selected
                ):
                    raise OperatorError("vercel_environment_collision")
                if len({item["key"] for item in selected}) != len(selected):
                    raise OperatorError("vercel_environment_collision")
            except (KeyError, TypeError):
                raise OperatorError("invalid_vercel_response") from None
            receipt = (
                read_private_json(self.receipt_file)
                if self.receipt_file.exists()
                else None
            )
            binding = {
                "team_id": self.target.team_id,
                "project_id": self.target.project_id,
                "supabase_ref": self.config.project_ref,
                "cluster_id": str(state.cluster_id),
            }
            if receipt and (receipt.get("v") != 1 or receipt.get("binding") != binding):
                raise OperatorError("vercel_receipt_target_mismatch")
            if not receipt:
                history = await self.request(
                    "/v6/deployments", projectId=self.target.project_id, limit=1
                )
                if selected or history.get("deployments"):
                    if (
                        not adopt_existing
                        or len(selected) != len(values)
                        or not await self.health(state)
                    ):
                        raise OperatorError("vercel_project_collision")
            wheel_digest = hashlib.sha256(Path(wheel).read_bytes()).hexdigest()
            configuration_digest = hashlib.sha256(
                json.dumps(values, sort_keys=True).encode()
            ).hexdigest()
            if receipt and receipt.get("creating"):
                # Never start another deployment while a prior outcome is unknown,
                # even if the operator supplied a different release meanwhile.
                receipt["deployment_id"] = await self.find_operation(receipt)
                receipt["creating"] = False
                write_private_json(self.receipt_file, receipt)
            same = (
                receipt
                and receipt["wheel_sha256"] == wheel_digest
                and receipt["configuration_sha256"] == configuration_digest
            )
            if receipt and receipt.get("deployment_id"):
                previous = await self.request(
                    "/v13/deployments/" + receipt["deployment_id"]
                )
                if (
                    previous.get("projectId") != self.target.project_id
                    or previous.get("meta", {}).get("oshepherd_operation")
                    != receipt["operation_id"]
                ):
                    raise OperatorError("vercel_receipt_deployment_mismatch")
                if not same and previous.get("readyState") not in (
                    "READY",
                    "ERROR",
                    "CANCELED",
                ):
                    raise OperatorError("vercel_previous_deployment_pending")
            if not same or not receipt.get("deployment_id"):
                # Always create a clean export; no user directory/private file is
                # uploaded. Verification of wheel/assets happens before writes.
                bundle = self.directory / "vercel-bundles" / str(uuid4())
                await asyncio.to_thread(export_vercel, bundle, wheel)
                receipt = (
                    receipt
                    if same
                    else {
                        "v": 1,
                        "binding": binding,
                        "wheel_sha256": wheel_digest,
                        "configuration_sha256": configuration_digest,
                        "operation_id": str(uuid4()),
                        "started_at": int(time.time() * 1000),
                        "creating": False,
                        "deployment_id": None,
                        "gateway_verified": False,
                    }
                )
                write_private_json(self.receipt_file, receipt)
                await self.request(
                    "/v10/projects/" + self.target.project_id + "/env",
                    method="POST",
                    upsert="true",
                    body=[
                        {
                            "key": key,
                            "value": value,
                            "type": "encrypted",
                            "target": ["production"],
                        }
                        for key, value in values.items()
                    ],
                )
                confirmed = await self.request(
                    "/v9/projects/" + self.target.project_id + "/env"
                )
                records = confirmed.get("envs")
                if not isinstance(records, list) or any(
                    not isinstance(item, dict) for item in records
                ):
                    raise OperatorError("invalid_vercel_response")
                owned = [item for item in records if item.get("key") in values]
                if (
                    len(owned) != len(values)
                    or {item.get("key") for item in owned} != set(values)
                    or any(
                        item.get("target") != ["production"]
                        or item.get("type") != "encrypted"
                        or item.get("gitBranch")
                        or item.get("customEnvironmentIds")
                        for item in owned
                    )
                ):
                    raise OperatorError("vercel_environment_not_verified")
                # Only exact verified export files, never a recursive user/state
                # upload. The manifest wheel contains no operator credentials.
                names = {
                    "app.py",
                    "vercel.json",
                    "runtime-requirements.txt",
                    ".python-version",
                    ".vercelignore",
                    "pyproject.toml",
                    "requirements.txt",
                    "uv.lock",
                    "oshepherd-manifest.json",
                    "vendor/" + Path(wheel).name,
                }
                file_records = []
                total = 0
                for name in sorted(names):
                    file = bundle / name
                    if file.is_symlink() or not file.is_file():
                        raise OperatorError("invalid_vercel_bundle")
                    data = file.read_bytes()
                    total += len(data)
                    if total > 8 * 1024 * 1024:
                        raise OperatorError("vercel_bundle_too_large")
                    file_records.append(
                        {
                            "file": name,
                            "encoding": "base64",
                            "data": base64.b64encode(data).decode(),
                        }
                    )
                # Journal before POST. Lost response is reconciled by operation
                # metadata; this non-idempotent request is never retried blindly.
                receipt["creating"] = True
                write_private_json(self.receipt_file, receipt)
                created = await self.request(
                    "/v13/deployments",
                    method="POST",
                    body={
                        "name": project["name"],
                        "project": self.target.project_id,
                        "target": "production",
                        "files": file_records,
                        "projectSettings": {"framework": "fastapi"},
                        "meta": {
                            "oshepherd_cluster": str(state.cluster_id),
                            "oshepherd_operation": receipt["operation_id"],
                        },
                    },
                )
                identifier = created.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(
                    r"dpl_[A-Za-z0-9]+", identifier
                ):
                    raise OperatorError("invalid_vercel_deployment_id")
                receipt["deployment_id"] = identifier
                receipt["creating"] = False
                write_private_json(self.receipt_file, receipt)
            identifier = receipt["deployment_id"]
            if not isinstance(identifier, str) or not re.fullmatch(
                r"dpl_[A-Za-z0-9]+", identifier
            ):
                raise OperatorError("invalid_vercel_deployment_id")
            deadline = asyncio.get_running_loop().time() + 300
            while True:
                deployment = await self.request("/v13/deployments/" + identifier)
                if (
                    deployment.get("projectId") != self.target.project_id
                    or deployment.get("target") != "production"
                    or deployment.get("meta", {}).get("oshepherd_operation")
                    != receipt["operation_id"]
                ):
                    raise OperatorError("vercel_receipt_deployment_mismatch")
                if deployment.get("readyState") == "READY":
                    break
                if deployment.get("readyState") in ("ERROR", "CANCELED"):
                    raise OperatorError("vercel_deployment_failed")
                if asyncio.get_running_loop().time() >= deadline:
                    raise OperatorError("vercel_deployment_not_ready")
                await asyncio.sleep(2)
            # The stable project domain is independent of a READY build record.
            await self.preflight()
            if not await self.health(state):
                raise OperatorError("vercel_gateway_not_verified")
            receipt["gateway_verified"] = True
            write_private_json(self.receipt_file, receipt)
            return {
                "gateway_deployed": True,
                "gateway_verified": True,
                "deployment_id": identifier,
                "hosted_qualified": False,
            }

    async def removal_plan(self):
        await self.preflight()
        state = Installation.load(self.directory, self.config)
        receipt = read_private_json(self.receipt_file)
        binding = {
            "team_id": self.target.team_id,
            "project_id": self.target.project_id,
            "supabase_ref": self.config.project_ref,
            "cluster_id": str(state.cluster_id),
        }
        identifier = receipt.get("deployment_id")
        if (
            receipt.get("v") != 1
            or receipt.get("binding") != binding
            or receipt.get("creating")
            or not isinstance(identifier, str)
            or not re.fullmatch(r"dpl_[A-Za-z0-9]+", identifier)
        ):
            raise OperatorError("vercel_owned_receipt_required")
        present = False
        if not receipt.get("removed"):
            try:
                deployment = await self.request("/v13/deployments/" + identifier)
            except OperatorError as error:
                if str(error) != "vercel_resource_missing":
                    raise
            else:
                present = True
                if (
                    deployment.get("projectId") != self.target.project_id
                    or deployment.get("meta", {}).get("oshepherd_operation")
                    != receipt["operation_id"]
                ):
                    raise OperatorError("vercel_receipt_deployment_mismatch")
        return {
            "project_id": self.target.project_id,
            "deployment_id": identifier,
            "deployment_present": present,
            "disable_cluster_first": str(state.cluster_id),
            "destructive": True,
            "preserve": [
                "provider_project",
                "other_deployments",
                "project_environments",
                "database",
                "results",
                "shared_schedules",
            ],
        }

    async def remove(self, *, expected_deployment_id, operator):
        plan = await self.removal_plan()
        if (
            expected_deployment_id != plan["deployment_id"]
            or operator.directory != self.directory.absolute()
            or operator.config != self.config
        ):
            raise OperatorError("vercel_removal_target_mismatch")
        await operator.disable()
        with installation_lock(self.directory):
            if Installation.load(self.directory, self.config).stage != "disabled":
                raise OperatorError("vercel_removal_requires_disabled_cluster")
            receipt = read_private_json(self.receipt_file)
            if not receipt.get("removed"):
                current = await self.removal_plan()
                receipt["removing"] = True
                write_private_json(self.receipt_file, receipt)
                if current["deployment_present"]:
                    await self.request(
                        "/v13/deployments/" + expected_deployment_id, method="DELETE"
                    )
                    if (await self.removal_plan())["deployment_present"]:
                        raise OperatorError("vercel_removal_not_verified")
                receipt.update(removed=True, gateway_verified=False)
                write_private_json(self.receipt_file, receipt)
            return {
                "owned_deployment_removed": True,
                "data_preserved": True,
                "other_provider_resources_preserved": True,
            }
