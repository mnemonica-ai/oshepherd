"""Background service ownership and noninteractive restart contracts."""

import asyncio
import hashlib
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace
from uuid import uuid4

from click.testing import CliRunner
import pytest

from oshepherd.backends.supabase import service as module
from oshepherd.backends.supabase.join_cli import JoinState
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.sessions import WorkerSession
from oshepherd.backends.supabase.service_cli import service


@pytest.fixture
def joined(tmp_path):
    directory = tmp_path / "joined"
    identity = uuid4()
    state = JoinState(
        supabase_url="https://example.supabase.co",
        publishable_key="sb_publishable_privatefixture",
        schema="oshepherd_api",
        capacity=1,
        ollama_url="http://127.0.0.1:11434",
        model="model:latest",
        session_user_id=str(identity),
        accepted_invitation_digest="a" * 64,
    )
    state.save(directory / "join.json")
    WorkerSession(
        supabase_url=state.supabase_url,
        user_id=identity,
        access_token="secret-worker-jwt",
        refresh_token="secret-refresh-token",
        expires_at=9999999999,
    ).save(directory / "worker-session.json")
    module.record_readiness("join", directory)
    return directory


@pytest.fixture
def manager(monkeypatch):
    state = SimpleNamespace(
        calls=[], loaded=False, active=False, fail_start=False, fail_stop=False
    )

    def run(args, **kwargs):
        state.calls.append(args)
        stdout, code = b"", 0
        if "show" in args:
            stdout = b"loaded" if state.loaded else b"not-found"
        elif "print" in args or "is-active" in args:
            code = 0 if state.loaded else 3
            stdout = b"state = running" if state.active else b"state = waiting"
        elif "bootstrap" in args or "start" in args:
            state.loaded = state.active = True
            if state.fail_start:
                raise module.ServiceError("service_command_failed")
        elif "bootout" in args or "stop" in args:
            if state.fail_stop:
                raise module.ServiceError("service_command_failed")
            state.active = False
            if "bootout" in args:
                state.loaded = False
        elif "enable" in args:
            state.loaded = True
        elif "disable" in args:
            state.loaded = False
        return SimpleNamespace(
            returncode=code, stdout=stdout, stderr=b"secret-manager-error"
        )

    monkeypatch.setattr(module, "_run", run)
    runtime = {"python": "/durable/tools/python", "manifest": "a" * 64}
    monkeypatch.setattr(module, "prepare_runtime", lambda *args: runtime)
    monkeypatch.setattr(module, "validate_python", lambda *args: runtime)
    return state


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_install_restart_stop_uninstall_preserves_state_and_private_files(
    tmp_path, joined, manager, system
):
    owned = module.UserService("test", home=tmp_path / "home", system=system)
    result = owned.install("join", joined)
    assert result == {
        "installed": True,
        "started": True,
        "name": "test",
        "readiness": "pending",
    }
    assert owned.unit.stat().st_mode & 0o777 == 0o600
    assert owned.receipt.stat().st_mode & 0o777 == 0o600
    assert "secret-worker" not in owned.unit.read_text()
    assert "privatefixture" not in owned.unit.read_text()
    assert owned.status()["phase"] == "pending"
    assert owned.stop()["stopped"]
    assert owned.start()["started"]
    assert owned.uninstall()["state_preserved"]
    assert not owned.unit.exists() and not owned.receipt.exists()
    assert (joined / "worker-session.json").exists()
    assert all(isinstance(args, list) for args in manager.calls)
    assert "secret" not in json.dumps(manager.calls)


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_unit_render_preserves_argument_boundaries_and_does_not_use_shell(
    tmp_path, system
):
    owned = module.UserService(
        "test", home=tmp_path / 'space $HOME %n ; "quote"', system=system
    )
    content = owned.render("/durable/path with spaces/python").decode()
    if system == "Darwin":
        data = plistlib.loads(content.encode())
        assert data["ProgramArguments"][0] == "/durable/path with spaces/python"
        assert data["ProgramArguments"][-1] == str(owned.receipt)
        assert data["StandardErrorPath"] == "/dev/null"
    else:
        assert 'ExecStart="/durable/path with spaces/python" "-I" "-m"' in content
        assert "$$HOME" in content and "%%n" in content and '\\"quote\\"' in content
        assert "StandardError=null" in content
    assert "/bin/sh" not in content


@pytest.mark.parametrize(
    "name", ["../../other", "shell;whoami", "name\nInjected", "x.service", "-option"]
)
def test_invalid_names_rejected_before_manager(name, manager):
    with pytest.raises(module.ServiceError, match="invalid_name"):
        module.UserService(name)
    assert not manager.calls


def test_unsupported_platform_is_actionable(tmp_path):
    with pytest.raises(module.ServiceError, match="unsupported_platform"):
        module.UserService(home=tmp_path, system="Windows")


def test_install_requires_ready_marker_and_rejects_changed_binding(
    tmp_path, joined, manager
):
    (joined / "join-ready.json").unlink()
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    with pytest.raises(OSError):
        owned.install("join", joined)
    assert not owned.unit.exists()
    module.record_readiness("join", joined)
    state = read_private_json(joined / "join.json")
    state["model"] = "different-model"
    write_private_json(joined / "join.json", state)
    with pytest.raises(module.ServiceError, match="completed_foreground"):
        owned.install("join", joined)


def test_same_project_wrong_worker_refused(tmp_path, joined, manager):
    value = read_private_json(joined / "worker-session.json")
    value["user_id"] = str(uuid4())
    write_private_json(joined / "worker-session.json", value)
    with pytest.raises(module.ServiceError, match="identity_mismatch"):
        module.validate_target("join", joined)


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_failed_start_rolls_back_owned_registration(tmp_path, joined, manager, system):
    manager.fail_start = True
    owned = module.UserService(home=tmp_path / "home", system=system)
    with pytest.raises(module.ServiceError):
        owned.install("join", joined)
    assert not owned.unit.exists() and not owned.receipt.exists()
    assert not manager.active
    assert (joined / "worker-session.json").exists()
    assert any("stop" in args or "bootout" in args for args in manager.calls)


def test_failed_rollback_stop_keeps_ownership_for_recovery(tmp_path, joined, manager):
    manager.fail_start = manager.fail_stop = True
    owned = module.UserService(home=tmp_path / "home", system="Darwin")
    with pytest.raises(module.ServiceError):
        owned.install("join", joined)
    assert owned.unit.exists() and owned.receipt.exists()


def test_unrelated_file_and_loaded_namespace_are_never_stopped(
    tmp_path, joined, manager
):
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    owned.unit.parent.mkdir(parents=True)
    owned.unit.write_text("unrelated service")
    with pytest.raises(module.ServiceError, match="already_exists"):
        owned.install("join", joined)
    assert owned.unit.read_text() == "unrelated service" and manager.calls == []
    owned.unit.unlink()
    manager.loaded = True
    with pytest.raises(module.ServiceError, match="name_collision"):
        owned.install("join", joined)
    assert not any("stop" in args for args in manager.calls)


def test_changed_owned_unit_blocks_stop_uninstall(tmp_path, joined, manager):
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    owned.install("join", joined)
    owned.unit.write_text("somebody changed this file")
    manager.calls.clear()
    for action in (owned.stop, owned.uninstall):
        with pytest.raises(module.ServiceError, match="ownership_mismatch"):
            action()
    assert manager.calls == []


def test_unit_publication_race_preserves_unrelated_file(
    tmp_path, joined, manager, monkeypatch
):
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    original = module._exclusive

    def race(path, data):
        if path == owned.unit:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("racing writer")
        original(path, data)

    monkeypatch.setattr(module, "_exclusive", race)
    with pytest.raises(module.ServiceError, match="collision"):
        owned.install("join", joined)
    assert owned.unit.read_text() == "racing writer"
    assert not owned.receipt.exists()
    assert not any("stop" in args for args in manager.calls)


def test_cli_errors_do_not_echo_provider_or_manager_output(monkeypatch):
    def failed(*args, **kwargs):
        raise ValueError("private-provider-key")

    monkeypatch.setattr(module.UserService, "status", failed)
    result = CliRunner().invoke(service, ["status"])
    assert result.exit_code == 1
    assert "private-provider-key" not in result.output


@pytest.mark.parametrize(
    "changes",
    [
        {"editable": True},
        {"prefix": "/home/user/.cache/uv/tool"},
        {"module": "/source/oshepherd/__init__.py"},
        {"manifest": "b" * 64},
    ],
)
def test_ephemeral_editable_different_runtime_rejected(tmp_path, monkeypatch, changes):
    executable = tmp_path / "python"
    executable.write_text("fixture")
    executable.chmod(0o700)
    data = {
        "prefix": "/durable/runtime",
        "module": "/durable/runtime/lib/python3.12/site-packages/oshepherd/__init__.py",
        "editable": False,
        "manifest": hashlib.sha256(
            (Path(module.__file__).parent / "release" / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    data.update(changes)
    monkeypatch.setattr(
        module,
        "_run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(data).encode()),
    )
    with pytest.raises(module.ServiceError, match="durable_installed_python"):
        module.validate_python(executable)


def test_uvx_runtime_is_materialized_from_exact_installed_wheel(tmp_path, monkeypatch):
    from oshepherd.backends.supabase import runtime_bundle
    from oshepherd.backends.supabase.private_files import private_directory

    directory = private_directory(tmp_path / "service")
    wheel = directory / "candidate.whl"
    wheel.write_bytes(b"fixture")
    calls = []

    def validate(executable):
        if Path(executable) == Path(module.sys.executable):
            raise module.ServiceError("service_requires_durable_installed_python")
        return {"python": str(executable), "manifest": "a" * 64}

    monkeypatch.setattr(module, "validate_python", validate)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/tools/uv")
    monkeypatch.setattr(runtime_bundle, "materialize_current_wheel", lambda path: wheel)
    monkeypatch.setattr(module, "_run", lambda args, **kwargs: calls.append(args))
    runtime = module.prepare_runtime(directory)
    assert runtime["python"] == str(directory / "runtime" / "bin" / "python")
    assert calls[0][:3] == ["/tools/uv", "venv", "--clear"]
    assert str(wheel) + "[supabase]" in calls[1]
    assert "--reinstall-package" in calls[1]
    assert (directory / "runtime-owner.json").exists()
    assert not any("tool" in args or "oshepherd[supabase]" in args for args in calls)


def test_existing_unowned_runtime_is_preserved(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "owned-by-someone-else").write_text("preserve")
    monkeypatch.setattr(
        module,
        "validate_python",
        lambda *args: (_ for _ in ()).throw(module.ServiceError("ephemeral")),
    )
    monkeypatch.setattr(module.shutil, "which", lambda name: "/tools/uv")
    with pytest.raises(module.ServiceError, match="runtime_collision"):
        module.prepare_runtime(tmp_path)
    assert (runtime / "owned-by-someone-else").read_text() == "preserve"


def test_join_runner_uses_existing_session_and_reports_readiness(
    tmp_path, joined, manager, monkeypatch
):
    from oshepherd.backends.supabase import join_cli

    owned = module.UserService(home=tmp_path / "home", system="Linux")
    owned.install("join", joined)
    called = []

    async def prepare(directory, *, model):
        called.append((directory, model))
        return "restricted-config"

    async def run(config, *, on_ready):
        assert config == "restricted-config"
        on_ready()
        assert read_private_json(owned.status_file)["phase"] == "worker_connected"

    monkeypatch.setattr(join_cli, "prepare_join", prepare)
    monkeypatch.setattr(join_cli, "run_join", run)
    asyncio.run(module.resume_service(owned.receipt))
    assert called == [(joined, "model:latest")]
    assert read_private_json(owned.status_file)["phase"] == "stopped"


def test_status_does_not_reuse_dead_process_readiness(tmp_path, joined, manager):
    owned = module.UserService(home=tmp_path / "home", system="Darwin")
    owned.install("join", joined)
    write_private_json(
        owned.status_file, {"v": 1, "phase": "worker_connected", "pid": 123}
    )
    assert owned.status()["phase"] == "pending"


def test_service_share_resume_never_bootstraps_or_enrolls(tmp_path):
    from oshepherd.backends.supabase.operator import Installation, OperatorConfig
    from oshepherd.backends.supabase.sharing import run_share

    config = OperatorConfig(
        project_ref="local",
        supabase_url="http://127.0.0.1:55321",
        publishable_key="sb_publishable_fixturekey",
        secret_key="sb_secret_fixture",
    )
    state = Installation(
        project_ref="local",
        supabase_url=config.supabase_url,
        profile="edge_free",
        owner_subject=uuid4(),
        stage="configured",
    )
    state.save(tmp_path)

    class Operator:
        directory = tmp_path

        async def doctor(self):
            return {"enabled": True, "schema_matches": True, "gateway_available": True}

        async def enroll(self, *args, **kwargs):
            pytest.fail("service must never enroll")

        async def install(self, *args, **kwargs):
            pytest.fail("service must never bootstrap")

    operator = Operator()
    operator.config = config
    with pytest.raises(module.OperatorError, match="requires_saved_session"):
        asyncio.run(
            run_share(
                operator,
                model="model",
                ollama_url="http://127.0.0.1:11434",
                resume_only=True,
            )
        )
    state.stage = "initialized"
    state.save(tmp_path)
    with pytest.raises(module.OperatorError, match="configured_installation"):
        asyncio.run(
            run_share(
                operator,
                model="model",
                ollama_url="http://127.0.0.1:11434",
                resume_only=True,
            )
        )


def test_removal_recovers_after_reload_failure(tmp_path, joined, manager, monkeypatch):
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    owned.install("join", joined)
    original = module._run
    failed = False

    def run(args, **kwargs):
        nonlocal failed
        if "daemon-reload" in args and not failed:
            failed = True
            raise module.ServiceError("service_command_failed")
        # Observed on systemd 252: both fail once the unit file is gone.
        if {"disable", "stop"} & set(args) and not owned.unit.exists():
            raise module.ServiceError("service_command_failed")
        return original(args, **kwargs)

    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(module.ServiceError):
        owned.uninstall()
    assert not owned.unit.exists()
    assert read_private_json(owned.receipt)["stage"] == "removing"
    assert owned.status()["phase"] == "removal_pending"
    with pytest.raises(module.ServiceError, match="removal_pending"):
        owned.start()
    manager.calls.clear()
    assert owned.uninstall()["uninstalled"]
    assert any("daemon-reload" in args for args in manager.calls)
    assert not owned.receipt.exists()
    assert (joined / "worker-session.json").exists()


def test_install_interrupted_before_unit_is_recoverable(
    tmp_path, joined, manager, monkeypatch
):
    owned = module.UserService(home=tmp_path / "home", system="Linux")
    original = module._exclusive

    def interrupted(path, data):
        if path == owned.unit:
            raise KeyboardInterrupt
        original(path, data)

    monkeypatch.setattr(module, "_exclusive", interrupted)
    with pytest.raises(KeyboardInterrupt):
        owned.install("join", joined)
    monkeypatch.setattr(module, "_exclusive", original)
    assert read_private_json(owned.receipt)["stage"] == "installing"
    assert owned.status()["phase"] == "removal_pending"
    with pytest.raises(module.ServiceError, match="removal_pending"):
        owned.start()
    manager.calls.clear()
    # A registration here cannot be ours, so it must not be stopped.
    manager.loaded = True
    assert owned.uninstall()["uninstalled"]
    assert manager.calls == []
    manager.loaded = False
    assert owned.install("join", joined)["installed"]


def test_systemd_working_directory_is_a_raw_path(tmp_path):
    owned = module.UserService(home=tmp_path / "$literal 100%", system="Linux")
    content = owned.render("/durable/python").decode()
    working = next(
        line for line in content.splitlines() if line.startswith("WorkingDirectory=")
    )
    assert working == "WorkingDirectory=" + str(owned.directory).replace("%", "%%")
    execution = next(
        line for line in content.splitlines() if line.startswith("ExecStart=")
    )
    assert "$$literal" in execution


def test_launchd_stop_waits_for_registration_and_process_exit(tmp_path, monkeypatch):
    owned = module.UserService(home=tmp_path, system="Darwin")
    calls, signals = [], []
    printing = iter(
        [
            SimpleNamespace(returncode=0, stdout=b"state = running\npid = 123"),
            SimpleNamespace(returncode=0, stdout=b"state = exiting"),
            SimpleNamespace(returncode=113, stdout=b""),
            SimpleNamespace(returncode=113, stdout=b""),
        ]
    )

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "print" in args:
            return next(printing)
        return SimpleNamespace(returncode=0, stdout=b"")

    def alive(pid, sig):
        signals.append((pid, sig))
        if len(signals) == 3:
            raise ProcessLookupError()

    monkeypatch.setattr(module, "_run", run)
    monkeypatch.setattr(module.os, "kill", alive)
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    owned._manager("stop")
    assert signals == [(123, 0)] * 3
    assert next(kwargs for args, kwargs in calls if "bootout" in args)["timeout"] == 90
    assert len([args for args, _ in calls if "print" in args]) == 4


def test_systemd_stop_timeout_allows_configured_drain(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module, "_run", lambda args, **kwargs: calls.append((args, kwargs))
    )
    module.UserService(home=tmp_path, system="Linux")._manager("stop")
    assert calls[0][1]["timeout"] == 90


def test_unfinished_launchd_stop_preserves_ownership(
    tmp_path, joined, manager, monkeypatch
):
    owned = module.UserService(home=tmp_path / "home", system="Darwin")
    owned.install("join", joined)
    monkeypatch.setattr(
        module,
        "_run",
        lambda args, **kwargs: SimpleNamespace(returncode=0, stdout=b"state = exiting"),
    )
    ticks = iter([0, 91])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(module.ServiceError, match="stop_pending_retry"):
        owned.uninstall()
    assert owned.unit.exists() and owned.receipt.exists()
