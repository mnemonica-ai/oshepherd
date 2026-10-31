"""Exercise ManagementDatabase on a disposable fresh local Supabase stack.

This replaces only api.supabase.com: requests reach real Postgres through the
postgres-meta service, as the `postgres` role. It proves migration transactions
and recovery, not the hosted endpoint's response or error shapes. Refuses the
isolated development stack because it migrates and writes to its target.

uv run --locked python -m scripts.supabase_management_local_check \
  --status-file /private/stack-status.json --meta-url http://127.0.0.1:56380
"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from oshepherd.backends.supabase.client import RpcClient
from oshepherd.backends.supabase.management_database import ManagementDatabase
from oshepherd.backends.supabase.releases import (
    OperatorError,
    migration_path,
    release_assets,
)

REF = "a" * 20
ORGANIZATION = "local-check"
PROBE = (
    "begin; create table public.oshepherd_atomic_probe();"
    " do $$ begin raise exception 'oshepherd_schema_drift'; end $$; commit;"
)


def provider(meta_url, lose_acknowledgement):
    """Emulate the two Management endpoints the transport uses."""

    def handle(request):
        if request.url.path == "/v1/projects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": REF,
                        "organization_id": ORGANIZATION,
                        "status": "ACTIVE_HEALTHY",
                    }
                ],
            )
        query = json.loads(request.content)["query"]
        response = httpx.post(meta_url + "/query", json={"query": query}, timeout=60)
        if response.status_code == 400:
            return httpx.Response(
                400,
                json={
                    "message": "Failed to run sql query: "
                    + response.json()["formattedError"]
                },
            )
        if lose_acknowledgement["armed"] and "oshepherd_release_v1" in query:
            lose_acknowledgement["armed"] = False
            raise httpx.ReadTimeout("simulated lost acknowledgement")
        return httpx.Response(response.status_code, content=response.content)

    return httpx.MockTransport(handle)


def rejected(call):
    try:
        call()
    except OperatorError as error:
        return str(error)
    raise AssertionError("operator_error_expected")


async def bootstrap(api_url, secret_key):
    """Prove the migrated schema reached real PostgREST and Auth."""
    owner_email = f"owner-{uuid4()}@example.invalid"
    headers = {"apikey": secret_key, "Authorization": "Bearer " + secret_key}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            api_url + "/auth/v1/admin/users",
            headers=headers,
            json={"email": owner_email, "email_confirm": True},
        )
        response.raise_for_status()
        owner = response.json()["id"]
        cluster = str(uuid4())
        rpc = RpcClient(api_url, secret_key, client=client)
        await rpc.rpc(
            "bootstrap_cluster",
            {
                "cluster_id": cluster,
                "owner_subject": owner,
                "operation_id": str(uuid4()),
            },
        )
        info = await rpc.rpc("installation_info", {"cluster_id": cluster})
    assert info["enabled"] and info["owner_subject"] == owner, "bootstrap_mismatch"


def run(args):
    status = json.loads(args.status_file.read_text())
    assert status["API_URL"].startswith("http://127.0.0.1:"), "loopback_required"
    assert not status["API_URL"].endswith(":55321"), "disposable_stack_required"
    _, manifest = release_assets()
    expected = len(migration_path(manifest, []))
    lose_acknowledgement = {"armed": False}
    config = SimpleNamespace(
        project_ref=REF,
        supabase_url=f"https://{REF}.supabase.co",
        exposed_schemas=["public", "graphql_public"],
    )
    database = ManagementDatabase(
        config,
        "local-check-token",
        ORGANIZATION,
        client=httpx.Client(transport=provider(args.meta_url, lose_acknowledgement)),
    )
    history = "select count(*) from oshepherd.release_migrations;"
    report = {"hosted_endpoint_tested": False}

    role = database.execute(
        "select current_user, rolsuper from pg_roles where rolname=current_user;"
    )
    assert role == "postgres|f", "hosted_role_required"
    report["role"] = role

    assert rejected(lambda: database.execute(PROBE)) == "oshepherd_schema_drift"
    assert (
        database.execute("select to_regclass('public.oshepherd_atomic_probe') is null;")
        == "t"
    ), "failed_transaction_left_changes"
    report["failed_transaction_rolled_back"] = True

    if (
        database.execute(
            "select has_schema_privilege(current_user,'storage','USAGE WITH GRANT OPTION');"
        )
        != "t"
    ):
        assert rejected(database.migrate) == "oshepherd_database_privileges_required"
        assert (
            database.execute(
                "select to_regclass('oshepherd.release_migrations') is null;"
            )
            == "t"
        ), "privilege_gate_left_changes"
        report["privilege_gate_before_mutation"] = True
        return report

    lose_acknowledgement["armed"] = True
    assert (
        rejected(database.migrate) == "database_outcome_ambiguous_resume_installation"
    )
    assert database.execute(history) == "1", "lost_acknowledgement_not_committed"
    database.migrate()
    assert database.execute(history) == str(expected), "migration_count"
    fingerprint = database.execute("select oshepherd.schema_fingerprint();")
    database.migrate()
    assert database.execute(history) == str(expected), "migration_not_idempotent"
    assert (
        database.execute("select oshepherd.schema_fingerprint();") == fingerprint
    ), "schema_changed_on_rerun"
    report["migrations"] = expected
    report["lost_acknowledgement_reconciled"] = True

    database.schedules()
    database.schedules()
    report["cleanup_schedules"] = int(
        database.execute(
            "select count(*) from cron.job where username=current_user and jobname like 'oshepherd%';"
        )
    )
    assert report["cleanup_schedules"] == 2, "schedule_count"

    asyncio.run(bootstrap(status["API_URL"], status["SECRET_KEY"]))
    report["real_postgrest_auth_bootstrap"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--meta-url", required=True)
    print(json.dumps(run(parser.parse_args())))
