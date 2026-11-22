"""Local startup reads explicit status JSON without exposing credentials."""

import json
import stat
import subprocess
import sys

import pytest

from scripts import supabase_local as local


@pytest.fixture
def cli(monkeypatch, tmp_path):
    config = tmp_path / "supabase"
    config.mkdir()
    (config / "config.toml").write_text(
        'project_id = "oshepherd-development"\n[api]\nport = 55321\n'
    )
    (config / "toolchain.json").write_text('{"supabase_cli":"2.116.0"}')
    monkeypatch.setattr(local, "ROOT", tmp_path)
    monkeypatch.setattr(local, "STATE", tmp_path / ".oshepherd/local")
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "unrelated-cloud-token")
    connection = {
        "API_URL": "http://127.0.0.1:55321",
        "PUBLISHABLE_KEY": "sb_publishable_fixture",
        "SECRET_KEY": "sb_secret_fixture",
        "ANON_KEY": "anon-fixture",
        "SERVICE_ROLE_KEY": "service-role-fixture",
        "JWT_SECRET": "extra-secret-not-persisted",
    }
    calls = []
    replies = {
        "start": (
            0,
            "Supabase is already running.\nProject URL: http://127.0.0.1:55321\nSecret: sb_secret_fixture\n",
            "",
        ),
        "status": (
            0,
            json.dumps(connection, indent=2),
            "Some optional services are stopped.\n",
        ),
    }

    def run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert "SUPABASE_ACCESS_TOKEN" not in kwargs["env"]
        assert kwargs["capture_output"] and kwargs["text"]
        calls.append(command)
        if command == ["supabase", "--version"]:
            return subprocess.CompletedProcess(command, 0, "2.116.0\n", "")
        assert command in (
            ["supabase", "start", "-x", local.EXCLUDED],
            ["supabase", "status", "--output", "json"],
        )
        code, out, err = replies[command[1]]
        return subprocess.CompletedProcess(command, code, out, err)

    monkeypatch.setattr(local.subprocess, "run", run)

    def invoke(action):
        monkeypatch.setattr(sys, "argv", ["supabase_local", action])
        local.main()

    return invoke, calls, replies, connection


@pytest.mark.parametrize("action", ["start", "status"])
def test_connection_comes_from_explicit_status_json(cli, capsys, action):
    invoke, calls, _, connection = cli
    invoke(action)
    assert calls[-1] == ["supabase", "status", "--output", "json"]
    assert len(calls) == (3 if action == "start" else 2)
    saved = local.STATE / "connection.json"
    assert json.loads(saved.read_text()) == {
        key: value for key, value in connection.items() if key != "JWT_SECRET"
    }
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE((local.STATE / "status.log").stat().st_mode) == 0o600
    if action == "start":
        assert "already running" in (local.STATE / "start.log").read_text()
    output = capsys.readouterr()
    assert "Local stack ready" in output.out
    assert not any(
        value in output.out + output.err
        for key, value in connection.items()
        if key != "API_URL"
    )


@pytest.mark.parametrize("failure", ["start", "status", "json", "origin"])
def test_failed_start_or_status_preserves_existing_credentials(cli, capsys, failure):
    invoke, calls, replies, connection = cli
    local.STATE.mkdir(parents=True)
    saved = local.STATE / "connection.json"
    saved.write_text("previous-private-connection")
    if failure in ("start", "status"):
        replies[failure] = (1, "", "private diagnostic sb_secret_fixture")
        expected = f"Local Supabase {failure} failed"
    elif failure == "json":
        replies["status"] = (0, "No connection JSON", "")
        expected = "connection JSON"
    else:
        replies["status"] = (
            0,
            json.dumps({**connection, "API_URL": "https://other-project.invalid"}),
            "",
        )
        expected = "isolated local origin"
    with pytest.raises(SystemExit, match=expected):
        invoke("start")
    assert saved.read_text() == "previous-private-connection"
    if failure == "start":
        assert len(calls) == 2
    output = capsys.readouterr()
    assert "sb_secret_fixture" not in output.out + output.err
