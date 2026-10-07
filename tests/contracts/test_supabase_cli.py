import json
import os
import subprocess
import sys

import pytest

from oshepherd.cli import start_worker, start_api
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
