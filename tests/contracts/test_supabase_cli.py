import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from oshepherd.cli import main, start_worker, start_api
from oshepherd.backends.supabase.startup import read_enrollment


@pytest.mark.parametrize("command", [start_worker, start_api])
def test_backend_selection_preserves_redis_default_and_flag_precedence(
    monkeypatch, command
):
    monkeypatch.delenv("OSHEPHERD_BACKEND", raising=False)
    with command.make_context(command.name, []) as context:
        assert context.params["backend"] == "redis"
    monkeypatch.setenv("OSHEPHERD_BACKEND", "supabase")
    with command.make_context(command.name, []) as context:
        assert context.params["backend"] == "supabase"
    with command.make_context(command.name, ["--backend", "redis"]) as context:
        assert context.params["backend"] == "redis"


def test_supabase_startup_does_not_import_redis_or_celery():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import oshepherd.backends.supabase.startup; "
            "assert not any(name == 'redis' or name == 'celery' or name.startswith(('redis.', 'celery.')) for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_root_help_does_not_load_optional_onboarding_dependencies(monkeypatch):
    import importlib

    original = importlib.import_module

    def guarded(name, *args, **kwargs):
        if name.endswith((".share_cli", ".join_cli")):
            raise AssertionError("onboarding loaded by unrelated CLI help")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", guarded)
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert all(command in result.output for command in ("share", "join", "invite"))


@pytest.mark.parametrize("command", ["share", "join", "invite"])
def test_onboarding_commands_parse_their_own_help(command):
    result = CliRunner().invoke(main, [command, "--help"])
    assert result.exit_code == 0, result.output
    assert "--state-dir" in result.output


def test_enrollment_file_is_private_and_project_bound(tmp_path):
    path = tmp_path / "enrollment.json"
    path.write_text(
        json.dumps(
            {
                "v": 1,
                "supabase_url": "https://project.test",
                "token_hash": "single-use-token",
            }
        )
    )
    path.chmod(0o600)
    assert (
        read_enrollment(path, supabase_url="https://project.test") == "single-use-token"
    )
    with pytest.raises(ValueError, match="another project"):
        read_enrollment(path, supabase_url="https://other.test")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        read_enrollment(path, supabase_url="https://project.test")
