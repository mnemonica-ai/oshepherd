"""Explicit, owned per-user background services for completed onboarding.

Unit files contain a private receipt path, never connection secrets. The runner
revalidates saved identities and resumes the same foreground lifecycle.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from .private_files import private_directory, read_private_json, write_private_json
from .releases import OperatorError


class ServiceError(OperatorError):
    pass


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def readiness_binding(kind, directory):
    data = read_private_json(
        Path(directory) / ("share.json" if kind == "share" else "join.json")
    )
    if kind == "join":
        data = {
            key: data[key]
            for key in (
                "supabase_url",
                "publishable_key",
                "schema",
                "capacity",
                "ollama_url",
                "model",
                "session_user_id",
            )
        }
    else:
        installation = read_private_json(Path(directory) / "installation.json")
        enrollment = installation["workers"][data["worker_name"]]
        data = {
            "selection": data,
            "cluster_id": installation["cluster_id"],
            "owner_subject": installation["owner_subject"],
            "worker_subject": enrollment["auth_subject"],
            "worker_id": enrollment["worker_id"],
        }
    return _digest(data)


def record_readiness(kind, directory, *, model=None):
    write_private_json(
        Path(directory) / (kind + "-ready.json"),
        {"v": 1, "binding": readiness_binding(kind, directory), "model": model},
    )


def validate_target(kind, directory, connection_file=None):
    directory = Path(directory).absolute()
    marker = read_private_json(directory / (kind + "-ready.json"))
    if marker.get("v") != 1 or marker.get("binding") != readiness_binding(
        kind, directory
    ):
        raise ServiceError("service_requires_completed_foreground_onboarding")
    if kind == "join":
        from .join_cli import JoinState
        from .sessions import WorkerSession

        state = JoinState.model_validate(read_private_json(directory / "join.json"))
        config = state.worker_config(directory)
        session = WorkerSession.load(
            config.WORKER_SESSION_FILE, supabase_url=config.SUPABASE_URL
        )
        if not state.session_user_id or state.session_user_id != str(session.user_id):
            raise ServiceError("service_worker_identity_mismatch")
        return {
            "kind": kind,
            "state_dir": str(directory),
            "binding": marker["binding"],
            "model": state.model,
            "connection_file": None,
        }
    if kind != "share":
        raise ServiceError("service_invalid_kind")
    from .operator import Installation, OperatorConfig
    from .sharing import gateway_url
    from .sessions import WorkerSession

    path = Path(connection_file or directory / "connection.json").absolute()
    config = OperatorConfig.load(path)
    state = Installation.load(directory, config)
    selection = read_private_json(directory / "share.json")
    worker = state.workers.get(selection["worker_name"])
    session = WorkerSession.load(
        directory / (selection["worker_name"] + ".worker-session.json"),
        supabase_url=config.supabase_url,
    )
    if (
        state.stage != "configured"
        or state.rotation is not None
        or worker is None
        or worker.revocation_requested
        or worker.capacity != selection["capacity"]
        or worker.auth_subject != session.user_id
        or selection["profile"] != config.profile
        or selection["gateway_url"] != gateway_url(config)
        or selection["project_ref"] != config.project_ref
        or selection["supabase_url"] != config.supabase_url
        or selection["owner_subject"] != str(state.owner_subject)
        or not isinstance(marker.get("model"), str)
        or not marker["model"]
    ):
        raise ServiceError("service_requires_verified_sharing")
    return {
        "kind": kind,
        "state_dir": str(directory),
        "binding": marker["binding"],
        "model": marker["model"],
        "connection_file": str(path),
    }


def _run(args, *, allowed=(0,), timeout=30, env=None):
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        raise ServiceError("service_command_failed") from None
    if result.returncode not in allowed:
        raise ServiceError("service_command_failed")
    return result


_PROBE = """import hashlib, importlib.metadata, json, pathlib, sys
import oshepherd
from oshepherd.backends.supabase.releases import release_assets
release_assets()
p=pathlib.Path(oshepherd.__file__).resolve()
d=importlib.metadata.distribution("oshepherd")
r=d.read_text("direct_url.json")
m=p.parent/"backends"/"supabase"/"release"/"manifest.json"
print(json.dumps({"prefix":sys.prefix,"module":str(p),"editable":bool(r and json.loads(r).get("dir_info",{}).get("editable")),"manifest":hashlib.sha256(m.read_bytes()).hexdigest()}))
"""


def validate_python(executable):
    executable = Path(executable).absolute()
    try:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError()
        result = _run([str(executable), "-I", "-c", _PROBE])
        if len(result.stdout) > 8192:
            raise ValueError()
        data = json.loads(result.stdout)
        prefix = Path(data["prefix"]).resolve()
        module = Path(data["module"]).resolve()
        if (
            data["editable"]
            or not module.is_relative_to(prefix)
            or "site-packages" not in module.parts
            or any(
                part.lower() in {"cache", "caches", ".cache", "tmp", "temp"}
                for part in prefix.parts
            )
        ):
            raise ValueError()
        if (
            data["manifest"]
            != hashlib.sha256(
                (Path(__file__).parent / "release" / "manifest.json").read_bytes()
            ).hexdigest()
        ):
            raise ValueError()
        if not re.fullmatch(r"[a-f0-9]{64}", data["manifest"]):
            raise ValueError()
        return {"python": str(executable), "manifest": data["manifest"]}
    except (ValueError, KeyError, TypeError, OSError, ServiceError):
        raise ServiceError("service_requires_durable_installed_python") from None


def prepare_runtime(directory, executable=None):
    if executable is not None:
        return validate_python(executable)
    try:
        return validate_python(sys.executable)
    except ServiceError:
        pass
    # uvx environments are disposable. Rebuild this exact installed distribution
    # and install it into an owned durable environment, never a published fallback.
    from .runtime_bundle import materialize_current_wheel

    uv = shutil.which("uv")
    if uv is None:
        raise ServiceError("service_runtime_requires_uv")
    runtime = Path(directory) / "runtime"
    ownership = Path(directory) / "runtime-owner.json"
    if runtime.is_symlink():
        raise ServiceError("service_runtime_collision")
    if runtime.exists() and not ownership.exists():
        raise ServiceError("service_runtime_collision")
    if not ownership.exists():
        write_private_json(
            ownership, {"v": 1, "runtime": str(runtime), "id": str(uuid4())}
        )
    elif read_private_json(ownership).get("runtime") != str(runtime):
        raise ServiceError("service_runtime_collision")
    wheel = materialize_current_wheel(Path(directory) / "package")
    python = runtime / "bin" / "python"
    if not python.exists():
        _run(
            [uv, "venv", "--clear", "--python", sys.executable, str(runtime)],
            timeout=120,
        )
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--reinstall-package",
            "oshepherd",
            str(wheel) + "[supabase]",
        ],
        timeout=300,
    )
    return validate_python(python)


def _exclusive(path, data):
    directory = private_directory(Path(path).parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".service-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ServiceError("service_file_collision") from None
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.unlink(temporary)


def _read_unit(path):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ServiceError("service_file_collision")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise ServiceError("service_file_collision")
        data = stream.read(65537)
        if len(data) > 65536:
            raise ServiceError("service_file_collision")
        return data


def _systemd_text(value):
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ServiceError("service_invalid_path")
    return value.replace("%", "%%")


def _systemd_arg(value):
    value = _systemd_text(value).replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class UserService:
    def __init__(self, name="default", *, home=None, system=None, root=None):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise ServiceError("service_invalid_name")
        self.system = system or platform.system()
        if self.system not in {"Darwin", "Linux"}:
            raise ServiceError("service_unsupported_platform")
        self.home = Path(home or Path.home()).absolute()
        self.name = name
        self.label = "org.oshepherd." + name
        self.directory = (
            Path(root or self.home / ".local" / "state" / "oshepherd" / "services")
            / name
        )
        self.receipt = self.directory / "service.json"
        self.status_file = self.directory / "status.json"
        self.unit = (
            self.home / "Library" / "LaunchAgents" / (self.label + ".plist")
            if self.system == "Darwin"
            else self.home / ".config" / "systemd" / "user" / (self.label + ".service")
        )
        self.domain = "gui/" + str(os.getuid())

    def render(self, python):
        args = [
            str(python),
            "-I",
            "-m",
            "oshepherd.backends.supabase.service",
            "--receipt",
            str(self.receipt),
        ]
        if self.system == "Darwin":
            return plistlib.dumps(
                {
                    "Label": self.label,
                    "ProgramArguments": args,
                    "RunAtLoad": True,
                    "KeepAlive": {"SuccessfulExit": False},
                    "ThrottleInterval": 10,
                    "ExitTimeOut": 75,
                    "WorkingDirectory": str(self.directory),
                    "StandardOutPath": "/dev/null",
                    "StandardErrorPath": "/dev/null",
                    "Umask": 0o077,
                }
            )
        return (
            "[Unit]\nDescription=Oshepherd foreground lifecycle\n[Service]\nType=exec\nExecStart="
            + " ".join(_systemd_arg(arg) for arg in args)
            # A raw path: systemd rejects a quoted one as not absolute.
            + "\nWorkingDirectory="
            + _systemd_text(str(self.directory))
            + "\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=75\nUMask=0077\nStandardOutput=null\nStandardError=null\n[Install]\nWantedBy=default.target\n"
        ).encode()

    def owned(self):
        data = read_private_json(self.receipt)
        if (
            data.get("v") != 1
            or data.get("name") != self.name
            or data.get("system") != self.system
            or data.get("unit") != str(self.unit)
            or data.get("stage") not in {"installing", "installed", "removing"}
        ):
            raise ServiceError("service_ownership_mismatch")
        if self.unit.exists() or self.unit.is_symlink():
            if (
                data.get("unit_sha256")
                != hashlib.sha256(_read_unit(self.unit)).hexdigest()
            ):
                raise ServiceError("service_ownership_mismatch")
        elif data["stage"] == "installed":
            raise ServiceError("service_ownership_mismatch")
        return data

    def _manager(self, action):
        if self.system == "Darwin":
            previous_pid = None
            if action == "stop":
                previous = self._manager("status")
                match = re.search(rb"\bpid = ([0-9]+)", previous.stdout)
                if previous.returncode == 0 and match:
                    previous_pid = int(match.group(1))
            args = {
                "start": ["bootstrap", self.domain, str(self.unit)],
                "stop": ["bootout", self.domain + "/" + self.label],
                "status": ["print", self.domain + "/" + self.label],
            }
            deadline = time.monotonic() + 90 if action == "stop" else None
            result = _run(
                ["launchctl", *args[action]],
                allowed=(0, 3, 113) if action in {"stop", "status"} else (0,),
                timeout=90 if action == "stop" else 30,
            )
            if action == "stop":
                # bootout acknowledges before the service and its process have
                # necessarily disappeared. Keep ownership until both are gone;
                # otherwise an immediate reinstall races the old registration.
                while True:
                    registered = self._manager("status").returncode == 0
                    alive = False
                    if previous_pid:
                        try:
                            os.kill(previous_pid, 0)
                            alive = True
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            alive = True
                    if not registered and not alive:
                        break
                    if time.monotonic() >= deadline:
                        raise ServiceError("service_stop_pending_retry")
                    time.sleep(0.1)
            return result
        commands = {
            "start": ["start", self.unit.name],
            "stop": ["stop", self.unit.name],
            "status": ["is-active", self.unit.name],
        }
        return _run(
            ["systemctl", "--user", *commands[action]],
            allowed=(0, 3, 4) if action == "status" else (0,),
            timeout=90 if action == "stop" else 30,
        )

    def _registered(self):
        if self.system == "Darwin":
            result = self._manager("status")
            return result.returncode == 0
        result = _run(
            [
                "systemctl",
                "--user",
                "show",
                self.unit.name,
                "--property=LoadState",
                "--value",
            ]
        )
        return result.stdout.strip() != b"not-found"

    def install(self, kind, state_dir, *, connection_file=None, executable=None):
        from .operator import installation_lock

        with installation_lock(self.directory):
            if self.receipt.exists() or self.unit.exists() or self.unit.is_symlink():
                raise ServiceError("service_already_exists_use_start_or_uninstall")
            if self._registered():
                raise ServiceError("service_manager_name_collision")
            target = validate_target(kind, state_dir, connection_file)
            runtime = prepare_runtime(self.directory, executable)
            content = self.render(runtime["python"])
            data = {
                "v": 1,
                "stage": "installing",
                "id": str(uuid4()),
                "name": self.name,
                "system": self.system,
                "unit": str(self.unit),
                "unit_sha256": hashlib.sha256(content).hexdigest(),
                "runtime": runtime,
                "target": target,
            }
            # Durable ownership intent precedes installation and launch.
            _exclusive(self.receipt, json.dumps(data).encode())
            published = False
            try:
                _exclusive(self.unit, content)
                published = True
                if self.system == "Linux":
                    _run(["systemctl", "--user", "daemon-reload"])
                    _run(["systemctl", "--user", "enable", self.unit.name])
                self._manager("start")
                data["stage"] = "installed"
                write_private_json(self.receipt, data)
            except Exception:
                # A failed start may still have launched the child. Stop first;
                # retain owned state if stopping itself fails so recovery is safe.
                if published:
                    self._manager("stop")
                    self._remove_files(data)
                else:
                    self.receipt.unlink()
                raise
            return {
                "installed": True,
                "started": True,
                "name": self.name,
                "readiness": "pending",
            }

    def _remove_files(self, data):
        self.owned()
        data["stage"] = "removing"
        write_private_json(self.receipt, data)
        # A missing unit means an earlier attempt already disabled it, and
        # systemd rejects disabling a unit file that no longer exists.
        if self.system == "Linux" and self.unit.exists():
            _run(["systemctl", "--user", "disable", self.unit.name])
        self.unit.unlink(missing_ok=True)
        if self.system == "Linux":
            _run(["systemctl", "--user", "daemon-reload"])
        self.receipt.unlink()
        self.status_file.unlink(missing_ok=True)

    def start(self):
        from .operator import installation_lock

        with installation_lock(self.directory):
            data = self.owned()
            if data["stage"] == "removing" or not self.unit.exists():
                raise ServiceError("service_removal_pending_rerun_uninstall")
            if (
                validate_target(
                    data["target"]["kind"],
                    data["target"]["state_dir"],
                    data["target"]["connection_file"],
                )
                != data["target"]
            ):
                raise ServiceError("service_target_changed")
            if validate_python(data["runtime"]["python"]) != data["runtime"]:
                raise ServiceError("service_runtime_changed_reinstall_required")
            result = self._manager("status")
            if result.returncode == 0:
                if self.system == "Darwin" and not re.search(
                    rb"state = running\b", result.stdout
                ):
                    _run(["launchctl", "kickstart", self.domain + "/" + self.label])
                return {"started": True, "readiness": "inspect_status"}
            self._manager("start")
            return {"started": True, "readiness": "pending"}

    def stop(self):
        from .operator import installation_lock

        with installation_lock(self.directory):
            self.owned()
            self._manager("stop")
            write_private_json(self.status_file, {"v": 1, "phase": "stopped"})
        return {"stopped": True}

    def status(self):
        data = self.owned()
        if data["stage"] == "removing" or not self.unit.exists():
            return {
                "installed": True,
                "manager_active": False,
                "phase": "removal_pending",
                "name": self.name,
            }
        result = self._manager("status")
        active = result.returncode == 0
        runtime = (
            read_private_json(self.status_file) if self.status_file.exists() else {}
        )
        pid = None
        if self.system == "Darwin":
            active = active and bool(re.search(rb"state = running\b", result.stdout))
            match = re.search(rb"\bpid = ([0-9]+)", result.stdout)
            pid = int(match.group(1)) if match else None
        elif active:
            value = _run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    self.unit.name,
                    "--property=MainPID",
                    "--value",
                ]
            ).stdout.strip()
            pid = int(value) if value.isdigit() else None
        phase = (
            runtime.get("phase", "pending")
            if pid and pid == runtime.get("pid")
            else "pending"
        )
        return {
            "installed": True,
            "manager_active": active,
            "phase": phase if active else "stopped",
            "name": self.name,
        }

    def uninstall(self):
        from .operator import installation_lock

        with installation_lock(self.directory):
            data = self.owned()
            if data["stage"] == "installing" and not self.unit.exists():
                # Interrupted before publishing the unit: the manager never saw
                # it, so any registration under this name is someone else's.
                self.receipt.unlink()
            else:
                if data["stage"] != "removing" or self._registered():
                    self._manager("stop")
                self._remove_files(data)
        return {"uninstalled": True, "state_preserved": True, "runtime_preserved": True}


async def resume_service(receipt):
    data = read_private_json(receipt)
    if validate_python(data["runtime"]["python"]) != data["runtime"]:
        raise ServiceError("service_runtime_changed_reinstall_required")
    target = data["target"]
    directory = Path(target["state_dir"])
    if validate_target(target["kind"], directory, target["connection_file"]) != target:
        raise ServiceError("service_target_changed")
    status = Path(receipt).parent / "status.json"

    def progress(phase):
        write_private_json(
            status, {"v": 1, "phase": phase, "time": time.time(), "pid": os.getpid()}
        )

    progress("starting")
    try:
        if target["kind"] == "join":
            from .join_cli import prepare_join, run_join
            from .operator import installation_lock

            with installation_lock(directory):
                config = await prepare_join(directory, model=target["model"])
                await run_join(config, on_ready=lambda: progress("worker_connected"))
        else:
            from .operator import Operator, OperatorConfig
            from .sharing import run_share

            selection = read_private_json(directory / "share.json")
            operator = Operator(
                OperatorConfig.load(target["connection_file"]), directory
            )
            try:
                await run_share(
                    operator,
                    model=target["model"],
                    ollama_url=selection["ollama_url"],
                    worker_name=selection["worker_name"],
                    capacity=selection["capacity"],
                    on_event=progress,
                    resume_only=True,
                )
            finally:
                await operator.close()
        progress("stopped")
    except BaseException:
        progress("failed")
        raise


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(resume_service(Path(args.receipt)))
    except (Exception, KeyboardInterrupt):
        # No arbitrary provider/configuration exception text reaches service logs.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
