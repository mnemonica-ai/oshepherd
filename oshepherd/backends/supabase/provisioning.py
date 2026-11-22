"""Bounded Supabase creation with durable intent and no blind mutation retries.

Names include a random operation suffix because the provider has no documented
idempotency key for these endpoints. Discovery reconciles uncertain creation;
absence after a dispatched request never causes another POST.
"""

import asyncio
from pathlib import Path
import re
import secrets
import time
from uuid import UUID, uuid4

from .operator import installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError


async def organizations(provider):
    values = await provider.request("GET", "/v1/organizations")
    if not isinstance(values, list) or len(values) > 1000:
        raise OperatorError("invalid_provider_response")
    result = []
    for item in values:
        if (
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(key), str) or not item[key]
                for key in ("id", "slug", "name")
            )
            or not re.fullmatch(r"[A-Za-z0-9_-]+", item["slug"])
        ):
            raise OperatorError("invalid_provider_response")
        result.append({key: item[key] for key in ("id", "slug", "name")})
    if len({item["id"] for item in result}) != len(result):
        raise OperatorError("invalid_provider_response")
    return result


async def available_regions(provider, organization):
    data = await provider.request(
        "GET",
        "/v1/projects/available-regions",
        params={"organization_slug": organization["slug"]},
    )
    try:
        values = data["all"]["specific"]
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError()
        result = []
        for item in values:
            if (
                not isinstance(item, dict)
                or item.get("type") != "specific"
                or not isinstance(item.get("name"), str)
                or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d", item.get("code", ""))
            ):
                raise ValueError()
            # The provider marks capacity/other unavailable regions with status.
            if "status" not in item:
                result.append({"name": item["name"], "code": item["code"]})
        if not result or len({item["code"] for item in result}) != len(result):
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperatorError("available_regions_unavailable") from None


async def organization_is_free(provider, organization):
    """Return None only when the documented optional plan field is absent."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", organization["slug"]):
        raise OperatorError("provider_target_mismatch")
    value = await provider.request("GET", "/v1/organizations/" + organization["slug"])
    if not isinstance(value, dict) or value.get("id") != organization["id"]:
        raise OperatorError("provider_target_mismatch")
    if "plan" not in value:
        return None
    if value["plan"] != "free":
        raise OperatorError("free_organization_required")
    return True


def creation_record(directory, kind):
    if kind not in {"organization", "project"}:
        raise OperatorError("invalid_creation_kind")
    path = Path(directory) / (kind + "-create.json")
    if not path.exists():
        return None
    record = read_private_json(path)
    try:
        operation = UUID(record["operation_id"])
        valid = (
            record["v"] == 1
            and record["kind"] == kind
            and record["stage"] in {"prepared", "creating", "created"}
            and record["name"].endswith("-os-" + operation.hex[:12])
            and isinstance(record["baseline"], list)
            and all(isinstance(value, str) for value in record["baseline"])
        )
        if kind == "project":
            valid = (
                valid
                and isinstance(record["database_password"], str)
                and len(record["database_password"]) >= 32
            )
            valid = valid and all(
                isinstance(record["organization"][key], str)
                for key in ("id", "slug", "name")
            )
            valid = valid and isinstance(record["region"], str)
        if not valid:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperatorError("invalid_creation_record") from None
    return record


async def plan_creation(
    provider,
    directory,
    kind,
    name,
    *,
    organization=None,
    region=None,
    free_attested=False,
):
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9 -]{0,219}", name
    ):
        raise OperatorError("invalid_resource_name")
    with installation_lock(directory) as directory:
        previous = creation_record(directory, kind)
        if previous is not None:
            if (
                previous["requested_name"] != name
                or previous.get("organization") != organization
                or previous.get("region") != region
            ):
                raise OperatorError("creation_selection_mismatch")
            return previous
        if kind == "organization":
            inventory = await organizations(provider)
        elif kind == "project":
            if organization not in await organizations(provider):
                raise OperatorError("provider_target_mismatch")
            if region not in {
                item["code"] for item in await available_regions(provider, organization)
            }:
                raise OperatorError("project_region_unavailable")
            inventory = await provider.projects()
        else:
            raise OperatorError("invalid_creation_kind")
        operation = uuid4()
        record = {
            "v": 1,
            "kind": kind,
            "operation_id": str(operation),
            "requested_name": name,
            "name": name + "-os-" + operation.hex[:12],
            "organization": organization,
            "region": region,
            "stage": "prepared",
            "resource_id": None,
            "baseline": [item["id"] for item in inventory],
            "free_attested": free_attested is True,
        }
        if kind == "project":
            record["database_password"] = secrets.token_urlsafe(36)
        write_private_json(directory / (kind + "-create.json"), record)
        return record


async def execute_creation(provider, directory, kind):
    """Caller confirms the exact durable plan before this method dispatches POST."""
    with installation_lock(directory) as directory:
        record = creation_record(directory, kind)
        if record is None:
            raise OperatorError("creation_plan_required")
        path = directory / (kind + "-create.json")
        if kind == "project" and record["organization"] not in await organizations(
            provider
        ):
            raise OperatorError("provider_target_mismatch")
        if kind == "project":
            free = await organization_is_free(provider, record["organization"])
            if free is None and record.get("free_attested") is not True:
                raise OperatorError("free_organization_attestation_required")
        inventory = (
            await organizations(provider)
            if kind == "organization"
            else await provider.projects()
        )
        matches = [
            item
            for item in inventory
            if item["name"] == record["name"]
            and (
                kind == "organization"
                or item["organization_id"] == record["organization"]["id"]
            )
        ]
        if len(matches) > 1 or any(
            item["id"] in record["baseline"] for item in matches
        ):
            raise OperatorError("creation_resource_collision")
        if matches:
            if record["stage"] == "prepared" or (
                record["resource_id"] is not None
                and matches[0]["id"] != record["resource_id"]
            ):
                raise OperatorError("creation_resource_collision")
            if kind == "project" and matches[0]["region"] != record["region"]:
                raise OperatorError("creation_resource_collision")
            record.update(stage="created", resource_id=matches[0]["id"])
            write_private_json(path, record)
            return matches[0]
        if record["stage"] != "prepared":
            raise OperatorError(
                "creation_outcome_unresolved_retry_later_without_new_state"
            )
        payload = {"name": record["name"]}
        if kind == "project":
            if record["region"] not in {
                item["code"]
                for item in await available_regions(provider, record["organization"])
            }:
                raise OperatorError("project_region_unavailable")
            payload.update(
                organization_slug=record["organization"]["slug"],
                db_pass=record["database_password"],
                region_selection={"type": "specific", "code": record["region"]},
            )
        record["stage"] = "creating"
        write_private_json(path, record)
        response = await provider.request(
            "POST",
            "/v1/" + ("organizations" if kind == "organization" else "projects"),
            json=payload,
        )
        # Do not trust a response with an unrelated identity; always reconcile
        # against authoritative discovery before subsequent mutations.
        if not isinstance(response, dict) or not isinstance(response.get("id"), str):
            raise OperatorError("invalid_creation_response")
        record["resource_id"] = response["id"]
        write_private_json(path, record)
    return await execute_creation(provider, directory, kind)


async def wait_project(
    provider, project_ref, organization_id, *, timeout=300, interval=3
):
    deadline = time.monotonic() + timeout
    try:
        async with asyncio.timeout(timeout):
            while True:
                matches = [
                    item
                    for item in await provider.projects()
                    if item["id"] == project_ref
                ]
                if (
                    len(matches) != 1
                    or matches[0]["organization_id"] != organization_id
                ):
                    raise OperatorError("provider_target_mismatch")
                project = matches[0]
                if project["status"] == "ACTIVE_HEALTHY":
                    return project
                if project["status"] in {"REMOVED", "INIT_FAILED", "RESTORE_FAILED"}:
                    raise OperatorError("project_not_ready_retry_saved_setup")
                await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
    except TimeoutError:
        raise OperatorError("project_readiness_timeout_retry_saved_setup") from None
