import json
from importlib.resources import files

import httpx
import pytest

from oshepherd.backends.supabase.management_database import ManagementDatabase
from oshepherd.backends.supabase.operator import OperatorConfig
from oshepherd.backends.supabase.releases import OperatorError

REF = "a" * 20


def config():
    return OperatorConfig(
        project_ref=REF,
        supabase_url=f"https://{REF}.supabase.co",
        publishable_key="sb_publishable_fixturekey",
        secret_key="sb_secret_fixture",
        exposed_schemas=["public", "graphql_public"],
    )


def projects(organization="selected", status="ACTIVE_HEALTHY"):
    return [{"id": REF, "organization_id": organization, "status": status}]


def test_shared_engine_migrations_preserve_transaction_and_lock_over_management(
    monkeypatch,
):
    from oshepherd.backends.supabase import releases

    assets = files("oshepherd.backends.supabase").joinpath("release")
    manifest = json.loads(assets.joinpath("manifest.json").read_text())
    monkeypatch.setattr(releases, "release_assets", lambda: (assets, manifest))
    posts = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer private-pat"
        if request.method == "GET":
            return httpx.Response(200, json=projects())
        assert (
            str(request.url)
            == f"https://api.supabase.com/v1/projects/{REF}/database/query"
        )
        body = json.loads(request.content)
        assert body["read_only"] is False
        posts.append(body["query"])
        if "has_schema_privilege" in body["query"]:
            return httpx.Response(201, json=[{"allowed": True}])
        if body["query"].startswith("select pg_catalog.to_regclass"):
            return httpx.Response(201, json=[{"exists": False}])
        return httpx.Response(201, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        database = ManagementDatabase(
            config(), "private-pat", "selected", client=client
        )
        database.migrate()
        database.schedules()
    mutations = posts[2:]
    assert len(mutations) == len(manifest["migration_paths"][0]) + 1
    for query in mutations:
        assert query.lower().startswith("begin;")
        assert query.rstrip().lower().endswith("commit;")
        assert "pg_advisory_xact_lock" in query
    assert all(
        "supabase_migrations.schema_migrations" in query for query in mutations[:-1]
    )
    assert all("private-pat" not in query for query in posts)


@pytest.mark.parametrize(
    "inventory, code",
    [
        (projects("other"), "provider_target_mismatch"),
        (projects(status="INACTIVE"), "project_not_ready"),
        (projects() * 2, "provider_target_mismatch"),
    ],
)
def test_wrong_project_binding_never_sends_sql(inventory, code):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=inventory)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OperatorError, match=code):
            ManagementDatabase(
                config(), "private-pat", "selected", client=client
            ).execute("select true;")
    assert calls == ["GET"]


def test_ambiguous_write_is_not_retried_or_exposed():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=projects())
        raise httpx.ReadTimeout("private-pat raw SQL password")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OperatorError) as error:
            ManagementDatabase(
                config(), "private-pat", "selected", client=client
            ).execute("begin; commit;")
    assert str(error.value) == "database_outcome_ambiguous_resume_installation"
    assert calls == ["GET", "POST"]


def test_trickling_write_deadline_is_ambiguous_and_never_replayed(monkeypatch):
    from oshepherd.backends.supabase import management_database

    times = iter([0, 1, 2, 3, 69])
    monkeypatch.setattr(management_database.time, "monotonic", lambda: next(times))
    calls = []

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"["
            yield b"]"

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=projects())
        return httpx.Response(201, stream=SlowBody())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            OperatorError, match="database_outcome_ambiguous_resume_installation"
        ):
            ManagementDatabase(
                config(), "private-pat", "selected", client=client
            ).execute("begin; commit;")
    assert calls == ["GET", "POST"]


@pytest.mark.parametrize(
    "body, expected",
    [
        (
            {
                "message": "Failed to run sql query: ERROR: P0001: oshepherd_schema_drift\nCONTEXT: private SQL"
            },
            "oshepherd_schema_drift",
        ),
        (
            {
                "message": "Failed to run sql query: ERROR: unknown failure\nCONTEXT: raise exception 'oshepherd_schema_drift'; private-pat"
            },
            "migration_failed",
        ),
        ({"message": ["private-pat"]}, "migration_failed"),
    ],
)
def test_only_known_primary_database_error_is_returned(body, expected):
    def handler(request):
        return (
            httpx.Response(200, json=projects())
            if request.method == "GET"
            else httpx.Response(400, json=body)
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OperatorError) as error:
            ManagementDatabase(
                config(), "private-pat", "selected", client=client
            ).execute("select true;")
    assert str(error.value) == expected


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([{"exists": True}], "t"),
        ([{"exists": False}], "f"),
        (
            [{"migrations": [{"version": "v1", "sha256": "hash"}]}],
            '[{"version":"v1","sha256":"hash"}]',
        ),
        ([], ""),
    ],
)
def test_provider_rows_feed_existing_engine_scalar_reads(rows, expected):
    def handler(request):
        return (
            httpx.Response(200, json=projects())
            if request.method == "GET"
            else httpx.Response(201, json=rows)
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert (
            ManagementDatabase(
                config(), "private-pat", "selected", client=client
            ).execute("select true;")
            == expected
        )
