"""Bounded Supabase provider discovery, scoped to an explicitly selected target.

No organization/project creation or billing changes. Runtime secrets and OAuth
tokens never appear in errors. Existing SQL migrations remain in Database.
"""

import asyncio
import json
import re

import httpx

from .client import RpcError, bounded_response, bounded_retry_after
from .operator import OperatorConfig
from .releases import OperatorError


class SupabaseManagement:
    def __init__(self, access_token, *, client=None):
        self.access_token = access_token
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.owns_client = client is None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def request(self, method, path, **kwargs):
        # Paths are constructed only by methods below, never accepted from UI.
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(10):
                    async with self.client.stream(
                        method,
                        "https://api.supabase.com" + path,
                        headers={"Authorization": "Bearer " + self.access_token},
                        follow_redirects=False,
                        **kwargs,
                    ) as response:
                        content = await bounded_response(response, 1024 * 1024)
                        status = response.status_code
                        delay = bounded_retry_after(response.headers.get("Retry-After"))
                if status in (429, 502, 503, 504) and attempt + 1 < attempts:
                    await asyncio.sleep(delay if delay is not None else 0.5)
                    continue
                if status in (401, 403):
                    raise OperatorError("provider_access_denied_reauthorize")
                if status >= 300:
                    raise OperatorError("provider_request_failed")
                return json.loads(content) if content else None
            except (httpx.HTTPError, TimeoutError):
                if method == "GET" and attempt + 1 < attempts:
                    await asyncio.sleep(0.5)
                    continue
                raise OperatorError(
                    "provider_unavailable"
                    if method == "GET"
                    else "provider_outcome_ambiguous"
                ) from None
            except (ValueError, TypeError, RpcError):
                raise OperatorError("invalid_provider_response") from None

    async def projects(self):
        data = await self.request("GET", "/v1/projects")
        if not isinstance(data, list) or len(data) > 1000:
            raise OperatorError("invalid_provider_response")
        result = []
        for item in data:
            if not isinstance(item, dict) or not re.fullmatch(
                r"[a-z]{20}", str(item.get("id"))
            ):
                raise OperatorError("invalid_provider_response")
            if not isinstance(item.get("organization_id"), str):
                raise OperatorError("invalid_provider_response")
            result.append(
                {
                    key: item.get(key)
                    for key in ("id", "organization_id", "name", "region", "status")
                }
            )
        return result

    async def project(self, project_ref, organization_id):
        if not re.fullmatch(r"[a-z]{20}", project_ref):
            raise OperatorError("invalid_project_reference")
        matches = [item for item in await self.projects() if item["id"] == project_ref]
        if len(matches) != 1 or matches[0]["organization_id"] != organization_id:
            raise OperatorError("provider_target_mismatch")
        if matches[0]["status"] != "ACTIVE_HEALTHY":
            raise OperatorError("project_not_ready")
        return matches[0]

    async def configuration(self, project_ref, organization_id, database_url):
        await self.project(project_ref, organization_id)
        settings = await self.request("GET", f"/v1/projects/{project_ref}/postgrest")
        keys = await self.request(
            "GET", f"/v1/projects/{project_ref}/api-keys", params={"reveal": "true"}
        )
        try:
            schemas = [name.strip() for name in settings["db_schema"].split(",")]
            # Only the existing default keys; do not create or rotate provider keys.
            public = [
                item["api_key"]
                for item in keys
                if item.get("name") == "default" and item.get("type") == "publishable"
            ]
            secret = [
                item["api_key"]
                for item in keys
                if item.get("name") == "default" and item.get("type") == "secret"
            ]
            if not public:
                public = [
                    item["api_key"]
                    for item in keys
                    if item.get("name") == "anon" and item.get("type") == "legacy"
                ]
            if not secret:
                secret = [
                    item["api_key"]
                    for item in keys
                    if item.get("name") == "service_role"
                    and item.get("type") == "legacy"
                ]
            if len(public) != 1 or len(secret) != 1:
                raise ValueError()
            return OperatorConfig(
                project_ref=project_ref,
                supabase_url=f"https://{project_ref}.supabase.co",
                publishable_key=public[0],
                secret_key=secret[0],
                database_url=database_url,
                exposed_schemas=schemas,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OperatorError("provider_configuration_unavailable_use_cli") from None
