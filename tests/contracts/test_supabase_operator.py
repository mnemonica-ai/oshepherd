"""Offline release integrity, credential isolation and operator boundaries."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from click.testing import CliRunner
import pytest

from oshepherd.cli import main
from oshepherd.backends.supabase.operator import (
    Installation,
    OperatorConfig,
    installation_lock,
)
from oshepherd.backends.supabase.private_files import write_private_json
from oshepherd.backends.supabase.releases import (
    Database,
    OperatorError,
    SCHEDULES,
    database_environment,
    export_bundle,
    migration_path,
    release_assets,
)

ROOT = Path(__file__).resolve().parents[2]


def test_packaged_release_matches_checked_in_immutable_artifacts():
    assets, manifest = release_assets()
    assert manifest["profiles"]["edge_free"]["qualified"] is False
    assert set(manifest["profiles"]) == {"edge_free"}
    for entry in manifest["migrations"]:
        source = (ROOT / "supabase" / entry["path"]).read_bytes()
        assert source == assets.joinpath(entry["path"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == entry["sha256"]
        body, record = source.split(b"\n-- Release record.", 1)
        assert hashlib.sha256(body).hexdigest() == entry["content_sha256"]
        assert entry["content_sha256"].encode() in record
    for name in manifest["artifacts"]:
        source = ROOT / ("supabase/" + name if name.startswith("functions/") else name)
        assert source.read_bytes() == assets.joinpath(name).read_bytes()
    assert [
        (item["name"], item["interval"], item["command"])
        for item in manifest["schedules"]
    ] == [(name, interval, command) for name, (interval, command) in SCHEDULES.items()]


def test_bundle_contains_only_release_artifacts_and_refuses_existing_files(tmp_path):
    bundle = tmp_path / "bundle"
    export_bundle(bundle)
    assert (bundle / "supabase/functions/ollama/index.ts").is_file()
    assert len(list((bundle / "supabase/migrations").glob("*.sql"))) == 7
    assert not (bundle / ".oshepherd").exists()
    assert not list(bundle.rglob("*.env"))
    with pytest.raises(OperatorError, match="bundle_directory_not_empty"):
        export_bundle(bundle)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:credential@127.0.0.1:5432/postgres",
        "postgresql://postgres:credential@remote.test:55322/postgres",
        "postgresql://postgres:credential@127.0.0.1:55322/another",
        "postgresql://postgres:credential@127.0.0.1:55322/postgres?options=-cstatement_timeout=0",
    ],
)
def test_local_database_target_is_fixed_and_error_does_not_echo_credentials(url):
    with pytest.raises(OperatorError) as error:
        database_environment(url, "local")
    assert "credential" not in str(error.value)


def test_remote_database_binds_ref_and_enforces_tls():
    ref = "a" * 20
    direct = f"postgresql://postgres:credential@db.{ref}.supabase.co/postgres"
    assert database_environment(direct, ref)["PGSSLMODE"] == "require"
    with pytest.raises(OperatorError, match="database_target_mismatch"):
        database_environment(direct, "b" * 20)
    with pytest.raises(OperatorError, match="database_tls_required"):
        database_environment(direct + "?sslmode=disable", ref)
    pooled = f"postgresql://postgres.{ref}:credential@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
    assert database_environment(pooled, ref)["PGUSER"] == "postgres." + ref


def test_local_administrator_is_bound_to_the_isolated_stack():
    local = "postgresql://supabase_admin:credential@127.0.0.1:55322/postgres"
    assert database_environment(local, "local")["PGUSER"] == "supabase_admin"
    with pytest.raises(OperatorError, match="database_target_mismatch"):
        database_environment(local.replace("55322", "5432"), "local")


def test_migrate_rejects_missing_storage_grant_option_before_mutations():
    from pydantic import SecretStr

    calls = []

    def runner(command, **kwargs):
        calls.append(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout="f\n")

    database = Database(
        SimpleNamespace(
            project_ref="local",
            database_url=SecretStr(
                "postgresql://postgres:credential@127.0.0.1:55322/postgres"
            ),
        ),
        runner=runner,
    )
    with pytest.raises(OperatorError, match="oshepherd_database_privileges_required"):
        database.migrate()
    assert len(calls) == 1 and calls[0].startswith("select ")


def test_psql_errors_and_arguments_never_contain_credentials():
    from pydantic import SecretStr

    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["PGPASSWORD"] == "credential"
        return SimpleNamespace(
            returncode=1, stderr="private credential and SQL error", stdout=""
        )

    database = Database(
        SimpleNamespace(
            project_ref="local",
            database_url=SecretStr(
                "postgresql://postgres:credential@127.0.0.1:55322/postgres"
            ),
        ),
        runner=runner,
    )
    with pytest.raises(OperatorError, match="migration_failed") as error:
        database.execute("select 1;")
    assert "credential" not in str(error.value)
    assert all("credential" not in argument for argument in calls[0])


@pytest.mark.parametrize(
    "stderr, expected",
    [
        (
            "ERROR: there is no unique constraint matching given keys\nCONTEXT: raise exception 'oshepherd_resource_collision';",
            "migration_failed",
        ),
        (
            "ERROR: oshepherd_schema_drift\nCONTEXT: raise exception 'oshepherd_resource_collision';",
            "oshepherd_schema_drift",
        ),
        ("ERROR: private oshepherd_schema_drift details", "migration_failed"),
    ],
)
def test_database_classifies_only_exact_primary_errors(stderr, expected):
    from pydantic import SecretStr

    database = Database(
        SimpleNamespace(
            project_ref="local",
            database_url=SecretStr(
                "postgresql://postgres:credential@127.0.0.1:55322/postgres"
            ),
        ),
        runner=lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr),
    )
    with pytest.raises(OperatorError) as error:
        database.execute("select 1;")
    assert str(error.value) == expected


def test_migration_paths_preserve_legacy_history_and_reject_mixed_or_altered_records():
    _, manifest = release_assets()
    assert [item["version"] for item in migration_path(manifest, [])] == manifest[
        "migration_paths"
    ][0]
    entries = {item["version"]: item for item in manifest["migrations"]}

    def records(path):
        return [
            {"version": version, "sha256": entries[version]["content_sha256"]}
            for version in path
        ]

    for path in manifest["migration_paths"]:
        for count in range(1, len(path) + 1):
            assert [
                item["version"]
                for item in migration_path(manifest, records(path[:count]))
            ] == path
    for invalid in (
        records(["20261010000200"]),
        records(["20261010000100", "20261011000100"]),
        [{"version": "20261010000100", "sha256": "0" * 64}],
    ):
        with pytest.raises(OperatorError, match="oshepherd_migration_conflict"):
            migration_path(manifest, invalid)


def test_installation_lock_prevents_concurrent_journal_mutation(tmp_path):
    with installation_lock(tmp_path / "private"):
        with pytest.raises(OperatorError, match="installation_busy"):
            with installation_lock(tmp_path / "private"):
                pytest.fail("Concurrent writer acquired the installation lock")


def test_state_and_cli_errors_redact_credentials(tmp_path):
    state = Installation(
        project_ref="local",
        supabase_url="http://127.0.0.1:55321",
        profile="edge_free",
        owner_subject=uuid4(),
    )
    assert state.application_key.get_secret_value() not in repr(state)
    directory = tmp_path / "private"
    state.save(directory)
    config = OperatorConfig(
        project_ref="local",
        supabase_url=state.supabase_url,
        publishable_key="sb_publishable_fixture",
        secret_key="sb_secret_fixture",
    )
    assert "sb_secret_fixture" not in repr(config)
    assert Installation.load(directory, config).application_key == state.application_key
    connection = directory / "connection.json"
    write_private_json(
        connection,
        {"supabase_url": "secret-invalid-url", "secret_key": "sensitive-key"},
    )
    result = CliRunner().invoke(
        main,
        [
            "supabase",
            "doctor",
            "--connection-file",
            str(connection),
            "--state-dir",
            str(directory),
        ],
    )
    assert result.exit_code != 0
    assert (
        "sensitive-key" not in result.output
        and "secret-invalid-url" not in result.output
    )


def test_cli_help_exposes_operator_workflow_without_backend_initialization():
    result = CliRunner().invoke(main, ["supabase", "--help"])
    assert result.exit_code == 0
    for command in (
        "install",
        "doctor",
        "upgrade",
        "enroll-worker",
        "rotate-key",
        "revoke-worker",
        "uninstall",
        "export-bundle",
    ):
        assert command in result.output


def test_configure_imports_private_local_connection_and_refuses_overwrite(tmp_path):
    source = tmp_path / "private/local.json"
    target = tmp_path / "private/operator.json"
    write_private_json(
        source,
        {
            "API_URL": "http://127.0.0.1:55321",
            "PUBLISHABLE_KEY": "sb_publishable_fixture",
            "SECRET_KEY": "sb_secret_fixture",
        },
    )
    arguments = [
        "supabase",
        "configure",
        "--local-from",
        str(source),
        "--output",
        str(target),
    ]
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0
    assert "sb_secret_fixture" not in result.output
    assert OperatorConfig.load(target).project_ref == "local"
    assert target.stat().st_mode & 0o777 == 0o600
    assert CliRunner().invoke(main, arguments).exit_code != 0
