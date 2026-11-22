"""Resumable Edge deployment over verified assets and an ownership receipt.

No billing changes, project creation, schema deletion or implicit adoption.
An acknowledged or ambiguous mutation is reconciled before further writes.
"""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from .client import bounded_response, RpcError
from .operator import Installation, installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError, release_assets


class EdgeDeployment:
    def __init__(self, provider, config, directory, organization_id, *, client=None):
        if config.project_ref == "local" or config.profile != "edge_free":
            raise OperatorError("hosted_edge_profile_required")
        self.provider = provider
        self.config = config
        self.directory = Path(directory)
        self.organization_id = organization_id
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.owns_client = client is None
        self.base = "/v1/projects/" + config.project_ref
        self.receipt_file = self.directory / "edge-deployment.json"

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def inventory(self):
        await self.provider.project(self.config.project_ref, self.organization_id)
        functions = await self.provider.request("GET", self.base + "/functions")
        secrets = await self.provider.request("GET", self.base + "/secrets")
        if (
            not isinstance(functions, list)
            or not isinstance(secrets, list)
            or any(not isinstance(item, dict) for item in functions + secrets)
        ):
            raise OperatorError("invalid_provider_inventory")
        matches = [item for item in functions if item.get("slug") == "ollama"]
        if len(matches) > 1:
            raise OperatorError("edge_function_collision")
        names = [item.get("name") for item in secrets]
        if len(names) != len(set(names)):
            raise OperatorError("edge_secret_collision")
        return (matches[0] if matches else None), {
            item.get("name"): item.get("value") for item in secrets
        }

    def values(self, state):
        return {
            "OSHEPHERD_CLUSTER_ID": str(state.cluster_id),
            "OSHEPHERD_SCHEMA": "oshepherd_api",
            "OSHEPHERD_SUPABASE_URL": self.config.supabase_url,
            "OSHEPHERD_GATEWAY_KEY": self.config.secret_key.get_secret_value(),
            "OSHEPHERD_PUBLIC_URL": self.config.supabase_url,
        }

    async def verified_health(self, state):
        url = self.config.supabase_url + "/functions/v1/ollama/health"
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
                        "GET", url, headers=headers, follow_redirects=False
                    ) as response:
                        body = await bounded_response(response, 4096)
                        if response.status_code != (200 if authorized else 401):
                            return False
                        if authorized and json.loads(body) != {"status": 200}:
                            return False
            return True
        except (httpx.HTTPError, TimeoutError, ValueError, RpcError):
            return False

    def receipt(self, state):
        if not self.receipt_file.exists():
            return None
        value = read_private_json(self.receipt_file)
        if (
            value.get("v") != 1
            or value.get("project_ref") != self.config.project_ref
            or value.get("organization_id") != self.organization_id
            or value.get("cluster_id") != str(state.cluster_id)
            or value.get("stage")
            not in ("claimed", "secrets_ready", "deployed", "removing", "removed")
        ):
            raise OperatorError("edge_receipt_target_mismatch")
        return value

    async def deploy(self, *, adopt_existing=False):
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            if state.stage != "configured":
                raise OperatorError("installation_not_configured")
            root, manifest = release_assets()
            source_names = sorted(
                name
                for name in manifest["artifacts"]
                if name.startswith("functions/ollama/")
            )
            files = [
                (
                    "file",
                    (
                        # Match the official CLI's source-relative module URLs.
                        # Flattening filenames changes the provider's ESZIP hash
                        # even when every source byte is identical, preventing
                        # strict adoption of a CLI-deployed function.
                        "supabase/" + name,
                        root.joinpath(name).read_bytes(),
                        "application/typescript",
                    ),
                )
                for name in source_names
            ]
            artifact = hashlib.sha256(
                json.dumps(
                    {name: manifest["artifacts"][name] for name in source_names},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            current, secrets = await self.inventory()
            receipt = self.receipt(state)
            if receipt and receipt["stage"] in ("removing", "removed"):
                raise OperatorError("edge_removal_started")
            if receipt and current:
                if (
                    receipt.get("function_id")
                    and receipt["function_id"] != current.get("id")
                ) or receipt["function_name"] != current.get("name"):
                    raise OperatorError("edge_function_collision")
            if not receipt and current and not adopt_existing:
                raise OperatorError("edge_function_collision")
            values = self.values(state)
            hashes = {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in values.items()
            }
            if not receipt and not current and any(name in secrets for name in values):
                raise OperatorError("edge_secret_collision")
            if receipt:
                for name, value in secrets.items():
                    if name in values and value not in (
                        receipt["secret_hashes"].get(name),
                        receipt.get("pending_secret_hashes", {}).get(name),
                        hashes[name],
                    ):
                        raise OperatorError("edge_secret_collision")
            elif current:
                if any(secrets.get(name) != hashes[name] for name in values):
                    raise OperatorError("edge_secret_collision")
                if not await self.verified_health(state):
                    raise OperatorError("edge_adoption_not_verified")
            name = (
                receipt["function_name"]
                if receipt
                else (
                    current["name"] if current else "oshepherd-" + str(state.cluster_id)
                )
            )
            metadata = {
                "name": name,
                "entrypoint_path": "supabase/functions/ollama/index.ts",
                "verify_jwt": False,
            }
            # Bundling has no deployment side effect. Save its provider hash so
            # a lost deployment acknowledgement can be reconciled exactly.
            bundled = await self.provider.request(
                "POST",
                self.base + "/functions/deploy",
                params={"slug": "ollama", "bundleOnly": "1"},
                data={"metadata": json.dumps(metadata)},
                files=files,
            )
            digest = bundled.get("ezbr_sha256") if isinstance(bundled, dict) else None
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise OperatorError("invalid_edge_bundle_response")
            if not receipt and current and current.get("ezbr_sha256") != digest:
                raise OperatorError("edge_adoption_artifact_mismatch")
            receipt = receipt or {
                "v": 1,
                "project_ref": self.config.project_ref,
                "organization_id": self.organization_id,
                "cluster_id": str(state.cluster_id),
                "function_name": name,
                "function_id": current.get("id") if current else None,
                "stage": "claimed",
                "secret_hashes": {},
            }
            receipt.update(
                artifact_sha256=artifact,
                bundle_sha256=digest,
                pending_secret_hashes=hashes,
            )
            write_private_json(self.receipt_file, receipt)
            if any(secrets.get(key) != hashes[key] for key in values):
                await self.provider.request(
                    "POST",
                    self.base + "/secrets",
                    json=[
                        {"name": key, "value": value} for key, value in values.items()
                    ],
                )
            receipt["stage"] = "secrets_ready"
            receipt["secret_hashes"] = hashes
            receipt.pop("pending_secret_hashes", None)
            write_private_json(self.receipt_file, receipt)
            if (
                not current
                or current.get("ezbr_sha256") != digest
                or current.get("verify_jwt") is not False
                or current.get("status") != "ACTIVE"
            ):
                await self.provider.request(
                    "POST",
                    self.base + "/functions/deploy",
                    params={"slug": "ollama"},
                    data={"metadata": json.dumps(metadata)},
                    files=files,
                )
            current, live_secrets = await self.inventory()
            if (
                not current
                or current.get("name") != name
                or current.get("ezbr_sha256") != digest
                or current.get("verify_jwt") is not False
                or current.get("status") != "ACTIVE"
                or not isinstance(current.get("id"), str)
                or not current["id"]
                or (
                    receipt.get("function_id")
                    and current["id"] != receipt["function_id"]
                )
                or any(live_secrets.get(key) != hashes[key] for key in values)
                or not await self.verified_health(state)
            ):
                raise OperatorError("edge_deployment_not_verified")
            receipt.update(function_id=current["id"], stage="deployed")
            write_private_json(self.receipt_file, receipt)
            return {
                "gateway_deployed": True,
                "gateway_verified": True,
                "function_id": current["id"],
                "hosted_qualified": False,
            }

    async def removal_plan(self):
        state = Installation.load(self.directory, self.config)
        receipt = self.receipt(state)
        if not receipt or not receipt.get("function_id"):
            raise OperatorError("edge_owned_receipt_required")
        current, secrets = await self.inventory()
        if current and (
            current.get("id") != receipt["function_id"]
            or current.get("name") != receipt["function_name"]
            or current.get("ezbr_sha256") != receipt["bundle_sha256"]
            or any(
                secrets.get(key) != value
                for key, value in receipt["secret_hashes"].items()
            )
        ):
            raise OperatorError("edge_function_collision")
        return {
            "project_ref": self.config.project_ref,
            "function_id": receipt["function_id"],
            "slug": "ollama",
            "disable_cluster_first": str(state.cluster_id),
            "preserve": [
                "database",
                "results",
                "shared_schedules",
                "project_secrets",
                "provider_project",
            ],
            "destructive": True,
        }

    async def remove(self, *, expected_function_id, operator):
        # Caller must review removal_plan first. Operator.disable keeps admission
        # disabled even if the subsequent provider mutation is ambiguous.
        plan = await self.removal_plan()
        if (
            expected_function_id != plan["function_id"]
            or operator.directory != self.directory.absolute()
            or operator.config != self.config
        ):
            raise OperatorError("edge_removal_target_mismatch")
        await operator.disable()
        with installation_lock(self.directory):
            state = Installation.load(self.directory, self.config)
            if state.stage != "disabled":
                raise OperatorError("edge_removal_requires_disabled_cluster")
            receipt = self.receipt(state)
            current, _ = await self.inventory()
            if current and (
                current.get("id") != expected_function_id
                or current.get("name") != receipt["function_name"]
            ):
                raise OperatorError("edge_function_collision")
            receipt["stage"] = "removing"
            write_private_json(self.receipt_file, receipt)
            if current:
                await self.provider.request("DELETE", self.base + "/functions/ollama")
            current, _ = await self.inventory()
            if current:
                raise OperatorError("edge_removal_not_verified")
            receipt["stage"] = "removed"
            write_private_json(self.receipt_file, receipt)
            return {
                "gateway_removed": True,
                "data_preserved": True,
                "shared_configuration_preserved": True,
            }
