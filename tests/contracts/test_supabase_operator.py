"""Offline release integrity, credential isolation and operator boundaries."""

import hashlib
from fnmatch import fnmatchcase
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
    release_assets,
)

ROOT = Path(__file__).resolve().parents[2]


def test_vercel_upload_policy_excludes_environment_variants_and_worker_state():
    assets, _ = release_assets()
    rules = assets.joinpath("templates/vercel/.vercelignore").read_text().splitlines()
    for name in (
        ".env",
        ".env.local",
        ".env.production",
        ".env.development.local",
        "gateway.env",
        "local.worker-session.json",
    ):
        assert any(fnmatchcase(name, rule) for rule in rules)
    for name in ("app.py", "vercel.json", "uv.lock", "runtime-requirements.txt"):
        assert not any(fnmatchcase(name, rule) for rule in rules)


def test_packaged_release_matches_checked_in_immutable_artifacts():
    assets, manifest = release_assets()
    assert manifest["profiles"]["edge_free"]["qualified"] is False
    assert manifest["profiles"]["vercel_hobby"]["qualified"] is False
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
    assert len(list((bundle / "supabase/migrations").glob("*.sql"))) == 2
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
        "export-vercel",
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
