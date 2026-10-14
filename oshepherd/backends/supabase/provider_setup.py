"""Existing-project onboarding with private configuration and owned Auth identity.

No project creation, billing changes, or implicit adoption. Provider discovery uses
the Management API; migrations still require a privately supplied database URL.
"""

from pathlib import Path
from uuid import UUID, uuid4

from .operator import Installation, Operator, OperatorConfig, installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError, database_environment


async def discover_projects(provider):
    organizations = await provider.request("GET", "/v1/organizations")
    if not isinstance(organizations, list) or len(organizations) > 1000:
        raise OperatorError("invalid_provider_response")
    names = {}
    for item in organizations:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in names
            or not isinstance(item.get("name"), str)
        ):
            raise OperatorError("invalid_provider_response")
        names[item["id"]] = item["name"]
    projects = await provider.projects()
    if len({item["id"] for item in projects}) != len(projects):
        raise OperatorError("invalid_provider_response")
    return [
        {**item, "organization_name": names[item["organization_id"]]}
        for item in projects
        if item["organization_id"] in names
    ]


def private_configuration(config):
    values = config.model_dump(mode="json")
    for key in ("publishable_key", "secret_key", "database_url"):
        value = getattr(config, key)
        values[key] = value.get_secret_value() if value else None
    return values


async def prepare_existing_project(
    provider, directory, project_ref, organization_id, database_url
):
    """Discover keys/schemas and persist only the explicitly selected target.

    The caller obtains confirmation before invoking this function. This step makes
    no cloud mutations. A durable binding prevents another target replacing files
    after a partial write, and existing unowned files are never overwritten.
    """
    try:
        database_environment(database_url, project_ref)
    except (ValueError, TypeError, AttributeError):
        raise OperatorError("invalid_database_connection") from None
    config = await provider.configuration(project_ref, organization_id, database_url)
    binding = {
        "v": 1,
        "project_ref": project_ref,
        "organization_id": organization_id,
        "profile": "edge_free",
    }
    connection = private_configuration(config)
    provider_values = {
        "organization_id": organization_id,
        "access_token": provider.access_token,
    }
    with installation_lock(directory) as directory:
        binding_file = directory / "provider-setup.json"
        if binding_file.exists():
            if read_private_json(binding_file) != binding:
                raise OperatorError("provider_setup_target_mismatch")
        else:
            if any(
                (directory / name).exists()
                for name in (
                    "connection.json",
                    "edge-provider.json",
                    "installation.json",
                )
            ):
                raise OperatorError("provider_setup_existing_state")
            write_private_json(binding_file, binding)
        path = directory / "connection.json"
        if path.exists():
            previous = read_private_json(path)
            if previous != connection and (directory / "installation.json").exists():
                saved_config = OperatorConfig.model_validate(previous)
                Installation.load(directory, saved_config)
                # A successful/partial migration appends our API schema. Keep
                # the original discovery snapshot; reject any unrelated drift.
                if (
                    saved_config.exposed_schemas is not None
                    and config.exposed_schemas is not None
                    and set(config.exposed_schemas)
                    == set(saved_config.exposed_schemas) | {"oshepherd_api"}
                ):
                    connection["exposed_schemas"] = saved_config.exposed_schemas
                    config.exposed_schemas = saved_config.exposed_schemas
            if previous != connection:
                raise OperatorError("provider_setup_configuration_changed")
        path = directory / "edge-provider.json"
        if path.exists():
            saved = read_private_json(path)
            if (
                not isinstance(saved, dict)
                or set(saved) != {"organization_id", "access_token"}
                or saved["organization_id"] != organization_id
            ):
                raise OperatorError("provider_setup_configuration_changed")
        # A freshly authorized PAT may replace an expired/revoked PAT, only after
        # provider discovery and all binding/configuration checks above succeed.
        write_private_json(directory / "connection.json", connection)
        write_private_json(directory / "edge-provider.json", provider_values)
    return config


async def check_new_edge_namespace(provider, config, directory):
    """Reject pre-existing gateway assets before creating a new owner identity."""
    if (Path(directory) / "installation.json").exists():
        # The deployment adapter validates the persisted installation/receipt.
        return
    base = f"/v1/projects/{config.project_ref}"
    functions = await provider.request("GET", base + "/functions")
    secrets = await provider.request("GET", base + "/secrets")
    if any(
        not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
        for items in (functions, secrets)
    ):
        raise OperatorError("invalid_provider_inventory")
    if any(item.get("slug") == "ollama" for item in functions):
        raise OperatorError("edge_function_collision")
    if any(str(item.get("name", "")).startswith("OSHEPHERD_") for item in secrets):
        raise OperatorError("edge_secret_collision")


def _owner_record(config, directory):
    filename = directory / "provider-owner.json"
    if filename.exists():
        record = read_private_json(filename)
        try:
            UUID(record["operation_id"])
            if record.get("owner_subject") is not None:
                UUID(record["owner_subject"])
            valid = (
                record["v"] == 1
                and record["project_ref"] == config.project_ref
                and record["supabase_url"] == config.supabase_url
                and record["stage"] in ("prepared", "creating", "ready")
                and (record["stage"] != "ready" or record.get("owner_subject"))
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise OperatorError("provider_owner_target_mismatch")
    else:
        if (directory / "installation.json").exists():
            raise OperatorError("provider_owner_existing_installation")
        record = {
            "v": 1,
            "project_ref": config.project_ref,
            "supabase_url": config.supabase_url,
            "operation_id": str(uuid4()),
            "owner_subject": None,
            "stage": "prepared",
        }
        write_private_json(filename, record)
    return filename, record


def _owned_subject(user, email, operation):
    try:
        if (
            user["email"] != email
            or user["app_metadata"]["oshepherd_owner_operation"] != operation
        ):
            raise ValueError()
        return UUID(user["id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OperatorError("provider_owner_identity_conflict") from None


async def ensure_owner(config, directory, *, client=None):
    """Create a non-login owner identity, journaled before writing to Auth.

    An uncertain POST is never blindly repeated. Exact provider-owned metadata
    reconciles a successful create whose acknowledgement was lost. A still absent
    identity requires operator reconciliation, avoiding duplicate creation.
    """
    operator = Operator(config, directory, client=client)
    try:
        with installation_lock(directory) as directory:
            filename, record = _owner_record(config, directory)
            operation = record["operation_id"]
            email = f"oshepherd-owner-{operation}@enrollment.invalid"
            if record["owner_subject"]:
                user = await operator.auth_request(
                    "GET", "users/" + record["owner_subject"]
                )
                subject = _owned_subject(user, email, operation)
                if str(subject) != record["owner_subject"]:
                    raise OperatorError("provider_owner_identity_conflict")
                return subject
            match = None
            for page in range(1, 101):
                result = await operator.auth_request(
                    "GET", "users", params={"page": page, "per_page": 100}
                )
                users = result.get("users") if isinstance(result, dict) else None
                if (
                    not isinstance(users, list)
                    or len(users) > 100
                    or any(not isinstance(user, dict) for user in users)
                ):
                    raise OperatorError("invalid_operator_auth_response")
                matches = [user for user in users if user.get("email") == email]
                if len(matches) > 1 or (matches and match is not None):
                    raise OperatorError("provider_owner_identity_conflict")
                if matches:
                    match = _owned_subject(matches[0], email, operation)
                if len(users) < 100:
                    break
            else:
                raise OperatorError("provider_owner_scan_limit")
            if match is None:
                if record["stage"] != "prepared":
                    raise OperatorError("provider_owner_creation_unresolved")
                record["stage"] = "creating"
                write_private_json(filename, record)
                user = await operator.auth_request(
                    "POST",
                    "users",
                    json={
                        "email": email,
                        "email_confirm": True,
                        "app_metadata": {"oshepherd_owner_operation": operation},
                    },
                )
                match = _owned_subject(user, email, operation)
            record.update(owner_subject=str(match), stage="ready")
            write_private_json(filename, record)
            return match
    finally:
        await operator.close()
