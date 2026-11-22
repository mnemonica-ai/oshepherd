"""Management SQL transport for the existing transactional migration engine.

The provider endpoint is experimental. A database URL/psql remains an operator
fallback, but guided setup requires neither. Never replay an ambiguous POST;
rerunning install reconciles the unchanged transactional migration history.
"""

import json
import re
import time

import httpx

from .releases import Database, OperatorError


class ManagementDatabase(Database):
    def __init__(self, config, access_token, organization_id, *, client=None):
        if (
            not re.fullmatch(r"[a-z]{20}", config.project_ref)
            or config.supabase_url != f"https://{config.project_ref}.supabase.co"
            or not isinstance(organization_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", organization_id)
            or not isinstance(access_token, str)
            or not 1 <= len(access_token) <= 8192
            or any(char.isspace() for char in access_token)
        ):
            raise OperatorError("invalid_management_database_target")
        if not config.exposed_schemas:
            raise OperatorError("existing_exposed_schemas_required")
        self.project_ref = config.project_ref
        self.organization_id = organization_id
        self._token = access_token
        self.client = client or httpx.Client(timeout=65, follow_redirects=False)
        self.owns_client = client is None
        self.exposed_schemas = config.exposed_schemas
        self.local_role_upgrade = False
        self._authorized = False

    def close(self):
        if self.owns_client:
            self.client.close()

    def _request(self, method, path, **kwargs):
        deadline = time.monotonic() + 65
        try:
            with self.client.stream(
                method,
                "https://api.supabase.com" + path,
                headers={"Authorization": "Bearer " + self._token},
                follow_redirects=False,
                **kwargs,
            ) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    # Bound trickling bodies as well as individual socket reads.
                    # One already-blocked socket read can consume its own timeout.
                    if time.monotonic() > deadline:
                        raise TimeoutError()
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise OperatorError("invalid_database_provider_response")
                    chunks.append(chunk)
                content = b"".join(chunks)
                if time.monotonic() > deadline:
                    raise TimeoutError()
                if response.status_code in (401, 403):
                    raise OperatorError("provider_access_denied_reauthorize")
                if response.status_code not in (200, 201):
                    # Error bodies may include SQL and credentials. Recognize only
                    # a primary, exact known database error; never echo raw text.
                    if method == "POST" and response.status_code == 400:
                        try:
                            message = json.loads(content).get("message", "")
                        except (ValueError, AttributeError):
                            message = ""
                        known = (
                            "oshepherd_resource_collision",
                            "oshepherd_schema_drift",
                            "oshepherd_migration_conflict",
                            "oshepherd_schedule_collision",
                            "oshepherd_bucket_mismatch",
                            "oshepherd_exposed_schema_missing",
                        )
                        match = re.search(
                            r"(?:^|\n)(?:Failed to run sql query: )?ERROR:\s*(?:P0001:\s*)?([^\n]+)",
                            message if isinstance(message, str) else "",
                        )
                        if match and match.group(1).strip() in known:
                            raise OperatorError(match.group(1).strip())
                        raise OperatorError("migration_failed")
                    raise OperatorError(
                        "database_outcome_ambiguous_resume_installation"
                        if method == "POST"
                        else "database_provider_unavailable"
                    )
                try:
                    return json.loads(content)
                except ValueError:
                    raise OperatorError("invalid_database_provider_response") from None
        except (httpx.HTTPError, TimeoutError):
            raise OperatorError(
                "database_outcome_ambiguous_resume_installation"
                if method == "POST"
                else "database_provider_unavailable"
            ) from None

    def authorize(self):
        projects = self._request("GET", "/v1/projects")
        if not isinstance(projects, list) or len(projects) > 1000:
            raise OperatorError("invalid_database_provider_response")
        matches = [
            item
            for item in projects
            if isinstance(item, dict) and item.get("id") == self.project_ref
        ]
        if (
            len(matches) != 1
            or matches[0].get("organization_id") != self.organization_id
        ):
            raise OperatorError("provider_target_mismatch")
        if matches[0].get("status") != "ACTIVE_HEALTHY":
            raise OperatorError("project_not_ready")
        self._authorized = True

    def execute(self, sql):
        if not isinstance(sql, str) or not sql or len(sql.encode()) > 1024 * 1024:
            raise OperatorError("invalid_migration_query")
        if not self._authorized:
            self.authorize()
        rows = self._request(
            "POST",
            f"/v1/projects/{self.project_ref}/database/query",
            json={"query": sql, "read_only": False},
        )
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise OperatorError("invalid_database_provider_response")

        def cell(value):
            if value is None:
                return ""
            if isinstance(value, bool):
                return "t" if value else "f"
            if isinstance(value, (dict, list)):
                return json.dumps(value, separators=(",", ":"))
            return str(value)

        # Match the shared engine's psql unaligned scalar/JSON reads. Mutation
        # output is ignored; each migration's entire BEGIN..COMMIT is one POST.
        return "\n".join(
            "|".join(cell(value) for value in row.values()) for row in rows
        )
