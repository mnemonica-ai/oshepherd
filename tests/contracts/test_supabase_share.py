"""Sharing resumes stable identities and supervises real readiness boundaries."""

import asyncio
from pathlib import Path
import time
from uuid import uuid4

from click.testing import CliRunner
import pytest

from oshepherd.backends.supabase import sharing
from oshepherd.backends.supabase.operator import (
    Enrollment,
    Installation,
    OperatorConfig,
)
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.releases import OperatorError
from oshepherd.backends.supabase.sessions import WorkerSession
from oshepherd.backends.supabase.share_cli import share


class FakeOperator:
    def __init__(self, directory):
        self.directory = directory
        self.config = OperatorConfig(
            project_ref="local",
            supabase_url="http://127.0.0.1:55321",
            publishable_key="sb_publishable_fixturekey",
            secret_key="sb_secret_fake",
            gateway_url="http://127.0.0.1:55326",
        )
        self.owner = uuid4()
        self.calls = []
        self.gateway_available = True
        self.fail_install = False

    async def install(self, owner):
        self.calls.append("install")
        if (self.directory / "installation.json").exists():
            state = Installation.load(self.directory, self.config)
        else:
            state = Installation(
                project_ref="local",
                supabase_url=self.config.supabase_url,
                profile="edge_free",
                owner_subject=owner,
            )
        state.save(self.directory)
        if self.fail_install:
            raise OperatorError("fixture_install_interrupted")
        state.stage = "configured"
        state.save(self.directory)
        write_private_json(
            self.directory / "client.json", {"application_key": "private-client"}
        )

    async def doctor(self):
        self.calls.append("doctor")
        return {
            "enabled": True,
            "schema_matches": True,
            "gateway_available": self.gateway_available,
        }

    async def enroll(self, name, capacity, *, renew=False):
        self.calls.append("enroll")
        state = Installation.load(self.directory, self.config)
        if name not in state.workers:
            state.workers[name] = Enrollment(
                capacity=capacity, auth_subject=uuid4(), worker_id=uuid4()
            )
            state.save(self.directory)
        challenge = self.directory / (name + ".enrollment.json")
        if renew or not challenge.exists():
            write_private_json(
                challenge,
                {
                    "v": 1,
                    "supabase_url": self.config.supabase_url,
                    "token_hash": "private-token-" + str(uuid4()),
                },
            )
        return {"enrollment_file": str(challenge)}

    async def verify_inference(self, model):
        self.calls.append("verify")
        return {"inference_verified": True, "model": model}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    operator = FakeOperator(tmp_path)
    events, stopped_services = [], []

    async def login(config, token):
        operator.calls.append("login")
        state = Installation.load(tmp_path, operator.config)
        WorkerSession(
            supabase_url=config.SUPABASE_URL,
            user_id=state.workers["local"].auth_subject,
            access_token="private-access",
            refresh_token="private-refresh",
            expires_at=int(time.time()) + 3600,
        ).save(config.WORKER_SESSION_FILE)

    async def worker(config, *, stop, ready):
        assert not hasattr(config, "SUPABASE_SECRET_KEY")
        ready.set()
        try:
            await stop.wait()
        finally:
            stopped_services.append("worker")

    async def maintenance(config, *, stop, ready, fail_fast):
        assert config.SUPABASE_SECRET_KEY.get_secret_value() == "sb_secret_fake"
        assert fail_fast
        ready.set()
        try:
            await stop.wait()
        finally:
            stopped_services.append("maintenance")

    monkeypatch.setattr(sharing, "login_worker", login)
    monkeypatch.setattr(sharing, "start_worker", worker)
    monkeypatch.setattr(sharing, "run_maintenance", maintenance)

    async def run(**kwargs):
        return await sharing.run_share(
            operator,
            model="fixture-model",
            ollama_url="http://127.0.0.1:11434",
            owner_subject=operator.owner,
            check=True,
            stop=asyncio.Event(),
            on_event=events.append,
            **kwargs,
        )

    return operator, run, events, stopped_services


def test_share_verifies_and_resumes_without_reinstalling_or_reenrolling(fixture):
    operator, run, events, stopped = fixture
    first = asyncio.run(run())
    state = Installation.load(operator.directory, operator.config)
    session = read_private_json(operator.directory / "local.worker-session.json")
    second = asyncio.run(run())
    after = Installation.load(operator.directory, operator.config)
    assert first["inference_verified"] is second["inference_verified"] is True
    assert first["hosted_qualified"] is False
    assert (state.cluster_id, state.key_id, state.workers) == (
        after.cluster_id,
        after.key_id,
        after.workers,
    )
    assert (
        read_private_json(operator.directory / "local.worker-session.json") == session
    )
    assert (
        operator.calls.count("install")
        == operator.calls.count("enroll")
        == operator.calls.count("login")
        == 1
    )
    assert operator.calls.count("verify") == 2
    assert events[:4] == [
        "cloud_ready",
        "worker_connected",
        "maintenance_ready",
        "inference_verified",
    ]
    assert sorted(stopped) == ["maintenance", "maintenance", "worker", "worker"]
    worker_env = (operator.directory / "local.worker.env").read_text()
    assert "sb_secret" not in worker_env and "private-client" not in worker_env


def test_interrupted_install_resumes_same_identity(fixture):
    operator, run, _, _ = fixture
    operator.fail_install = True
    with pytest.raises(OperatorError, match="fixture_install_interrupted"):
        asyncio.run(run())
    before = Installation.load(operator.directory, operator.config)
    operator.fail_install = False
    asyncio.run(run())
    after = Installation.load(operator.directory, operator.config)
    assert before.cluster_id == after.cluster_id
    assert before.application_key == after.application_key


def test_changed_gateway_rejected_before_external_call(fixture):
    operator, run, _, _ = fixture
    asyncio.run(run())
    operator.calls.clear()
    operator.config.gateway_url = "http://127.0.0.1:55327"
    with pytest.raises(OperatorError, match="sharing_configuration_mismatch"):
        asyncio.run(run())
    assert operator.calls == []


def test_disabled_state_cannot_be_reactivated(fixture):
    operator, run, _, _ = fixture
    asyncio.run(run())
    state = Installation.load(operator.directory, operator.config)
    state.stage = "disabled"
    state.save(operator.directory)
    operator.calls.clear()
    with pytest.raises(OperatorError, match="installation_disabled"):
        asyncio.run(run())
    assert operator.calls == []


def test_gateway_unavailable_never_enrolls_or_claims_readiness(fixture):
    operator, run, events, stopped = fixture
    operator.gateway_available = False
    with pytest.raises(OperatorError, match="sharing_gateway_unavailable"):
        asyncio.run(run())
    assert operator.calls == ["install", "doctor"]
    assert not events and not stopped


@pytest.mark.parametrize("service", ["worker", "maintenance"])
def test_early_service_exit_cannot_pass_even_if_ready_set(
    fixture, monkeypatch, service
):
    operator, run, events, stopped = fixture

    async def exit_early(config, *, stop, ready, **kwargs):
        ready.set()

    monkeypatch.setattr(
        sharing,
        "start_worker" if service == "worker" else "run_maintenance",
        exit_early,
    )
    with pytest.raises(OperatorError, match="sharing_" + service + "_stopped"):
        asyncio.run(run())
    assert "verify" not in operator.calls
    assert "inference_verified" not in events
    assert ("maintenance" if service == "worker" else "worker") in stopped


def test_cleanup_crash_during_inference_stops_worker_and_cancels_probe(
    fixture, monkeypatch
):
    operator, run, events, stopped = fixture
    probe_started = asyncio.Event()
    probe_cancelled = []

    async def verify(model):
        probe_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            probe_cancelled.append(True)

    async def maintenance(config, *, stop, ready, fail_fast):
        ready.set()
        await probe_started.wait()
        raise RuntimeError("secret-that-must-not-be-returned")

    operator.verify_inference = verify
    monkeypatch.setattr(sharing, "run_maintenance", maintenance)
    with pytest.raises(OperatorError, match="sharing_maintenance_stopped") as error:
        asyncio.run(run())
    assert "secret" not in str(error.value)
    assert probe_cancelled and "worker" in stopped
    assert "inference_verified" not in events


def test_failed_inference_stops_both_services_and_keeps_resumable_session(fixture):
    operator, run, events, stopped = fixture

    async def failed(model):
        raise OperatorError("inference_failed")

    operator.verify_inference = failed
    with pytest.raises(OperatorError, match="inference_failed"):
        asyncio.run(run())
    assert sorted(stopped) == ["maintenance", "worker"]
    assert "inference_verified" not in events
    assert (operator.directory / "local.worker-session.json").exists()


def test_ambiguous_auth_is_not_replayed_without_new_challenge(fixture, monkeypatch):
    operator, run, _, _ = fixture
    calls = []

    async def uncertain(config, token):
        calls.append(token)
        raise OperatorError("worker_auth_ambiguous")

    monkeypatch.setattr(sharing, "login_worker", uncertain)
    with pytest.raises(OperatorError, match="worker_auth_ambiguous"):
        asyncio.run(run())
    with pytest.raises(OperatorError, match="sharing_enrollment_ambiguous_use_renew"):
        asyncio.run(run())
    assert len(calls) == 1
    with pytest.raises(OperatorError, match="worker_auth_ambiguous"):
        asyncio.run(run(renew_enrollment=True))
    assert len(calls) == 2 and calls[0] != calls[1]
    assert (
        "private-token"
        not in (operator.directory / "local.share-login.json").read_text()
    )


def test_share_lock_rejects_concurrent_foreground_owner(fixture):
    operator, run, _, _ = fixture
    with sharing.sharing_lock(operator.directory):
        with pytest.raises(OperatorError, match="sharing_already_running"):
            asyncio.run(run())
    assert operator.calls == []


def test_fresh_login_wrong_identity_stops_before_worker(fixture, monkeypatch):
    operator, run, events, stopped = fixture

    async def wrong_identity(config, token):
        WorkerSession(
            supabase_url=config.SUPABASE_URL,
            user_id=uuid4(),
            access_token="private",
            refresh_token="private",
            expires_at=2**31,
        ).save(config.WORKER_SESSION_FILE)

    monkeypatch.setattr(sharing, "login_worker", wrong_identity)
    with pytest.raises(OperatorError, match="sharing_worker_session_conflict"):
        asyncio.run(run())
    assert events == ["cloud_ready"] and not stopped


@pytest.mark.parametrize("profile", ["edge_free"])
def test_completed_wizard_resume_does_not_need_provider_credentials(fixture, profile):
    from oshepherd.backends.supabase.share_cli import configured_selection
    from oshepherd.backends.supabase.provider_setup import private_configuration

    operator, run, _, _ = fixture
    asyncio.run(run())
    state = Installation.load(operator.directory, operator.config)
    operator.config = operator.config.model_copy(update={"profile": profile})
    state.profile = profile
    state.save(operator.directory)
    write_private_json(
        operator.directory / "connection.json", private_configuration(operator.config)
    )
    write_private_json(
        operator.directory / "provider-setup.json",
        {
            "v": 1,
            "project_ref": "local",
            "organization_id": "selected-org",
            "profile": profile,
        },
    )
    write_private_json(
        operator.directory / "provider-owner.json",
        {
            "v": 1,
            "project_ref": "local",
            "supabase_url": operator.config.supabase_url,
            "stage": "ready",
            "owner_subject": str(state.owner_subject),
        },
    )
    write_private_json(
        operator.directory / "edge-deployment.json",
        {
            "project_ref": "local",
            "organization_id": "selected-org",
            "cluster_id": str(state.cluster_id),
            "stage": "deployed",
        },
    )
    assert configured_selection(operator.directory) == operator.config
    assert not (operator.directory / "edge-provider.json").exists()
    record = read_private_json(operator.directory / "provider-owner.json")
    record["owner_subject"] = str(uuid4())
    write_private_json(operator.directory / "provider-owner.json", record)
    with pytest.raises(OperatorError, match="provider_setup_target_mismatch"):
        configured_selection(operator.directory)


@pytest.mark.parametrize("destination_race", [False, True])
def test_invite_exports_valid_private_file_and_preserves_racing_destination(
    tmp_path, monkeypatch, destination_race
):
    from oshepherd.backends.supabase import operator as operator_module
    from oshepherd.backends.supabase import onboarding
    from oshepherd.backends.supabase.provider_setup import private_configuration
    from oshepherd.backends.supabase.share_cli import invite

    target = tmp_path / "invitation.json"
    config_path = tmp_path / "connection.json"
    write_private_json(
        config_path, private_configuration(FakeOperator(tmp_path).config)
    )
    exported = []
    export_errors = []
    actual_export = onboarding.export_invitation

    def checked_export(config, enrollment_file, capacity, destination):
        # Explicitly validate fixture credentials so a validation failure cannot
        # make the overwrite regression pass without reaching the exporter.
        challenge = read_private_json(enrollment_file)
        onboarding.WorkerInvitation(
            supabase_url=config.supabase_url,
            publishable_key=config.publishable_key,
            token_hash=challenge["token_hash"],
            capacity=capacity,
        )
        exported.append(True)
        try:
            return actual_export(config, enrollment_file, capacity, destination)
        except onboarding.OnboardingError as error:
            export_errors.append(str(error))
            raise

    monkeypatch.setattr(onboarding, "export_invitation", checked_export)

    class RacingOperator(FakeOperator):
        def __init__(self, config, directory):
            super().__init__(directory)

        async def enroll(self, name, capacity, *, renew=False):
            if destination_race:
                write_private_json(target, {"preserve": "existing-data"})
            challenge = tmp_path / "challenge.json"
            write_private_json(
                challenge,
                {
                    "v": 1,
                    "supabase_url": self.config.supabase_url,
                    "token_hash": "private-token",
                },
            )
            return {"enrollment_file": str(challenge)}

        async def close(self):
            pass

    monkeypatch.setattr(operator_module, "Operator", RacingOperator)
    result = CliRunner().invoke(
        invite,
        [
            "--connection-file",
            str(config_path),
            "--state-dir",
            str(tmp_path),
            "--name",
            "remote",
            "--output",
            str(target),
        ],
    )
    assert exported == [True]
    if destination_race:
        assert result.exit_code != 0
        assert export_errors == [
            "Invitation output already exists; choose a new private file"
        ]
        assert (
            "Invitation output already exists; choose a new private file"
            in result.output
        )
        assert "Private invitation saved" not in result.output
        assert read_private_json(target) == {"preserve": "existing-data"}
    else:
        assert not export_errors
        assert result.exit_code == 0, result.output
        assert "Private invitation saved" in result.output
        invitation = onboarding.WorkerInvitation.load(target)
        assert invitation.token_hash.get_secret_value() == "private-token"
        assert (
            invitation.publishable_key.get_secret_value() == "sb_publishable_fixturekey"
        )
        assert target.stat().st_mode & 0o777 == 0o600
        assert "sb_secret" not in target.read_text()
    assert "private-token" not in result.output


def test_cli_redacts_invalid_private_configuration(tmp_path, monkeypatch):
    from oshepherd.backends.supabase import onboarding

    async def discover(*args, **kwargs):
        return onboarding.OllamaDiscovery(
            "http://127.0.0.1:11434", ("fixture",), "fixture"
        )

    monkeypatch.setattr(onboarding, "discover_ollama", discover)
    path = tmp_path / "connection.json"
    write_private_json(path, {"secret_key": "do-not-print-this"})
    result = CliRunner().invoke(
        share, ["--connection-file", str(path), "--state-dir", str(tmp_path), "--check"]
    )
    assert result.exit_code != 0
    assert "do-not-print-this" not in result.output
    assert "Inference verified" not in result.output


def test_cli_preserves_actionable_ollama_discovery_error(tmp_path, monkeypatch):
    from oshepherd.backends.supabase import onboarding

    async def missing_models(*args, **kwargs):
        raise onboarding.OnboardingError(
            "Ollama has no installed models; install a model separately, then retry"
        )

    monkeypatch.setattr(onboarding, "discover_ollama", missing_models)
    result = CliRunner().invoke(share, ["--state-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "Ollama has no installed models" in result.output
    assert "personal access token" not in result.output


@pytest.mark.parametrize("profile", ["edge_free"])
def test_guided_cli_passes_management_database_and_selected_gateway(
    tmp_path, monkeypatch, profile
):
    from oshepherd.backends.supabase import onboarding, operator as operator_module
    from oshepherd.backends.supabase import (
        provider_cli,
        share_cli,
        management_database,
    )

    config = OperatorConfig(
        project_ref="a" * 20,
        supabase_url="https://" + "a" * 20 + ".supabase.co",
        publishable_key="sb_publishable_fixturekey",
        secret_key="sb_secret_fake",
        profile=profile,
        exposed_schemas=["public"],
    )
    owner = uuid4()
    provider_path = tmp_path / "edge-provider.json"
    write_private_json(
        provider_path, {"access_token": "private-pat", "organization_id": "selected"}
    )
    events = []

    async def discover(*args, **kwargs):
        return onboarding.OllamaDiscovery(
            "http://127.0.0.1:11434", ("fixture",), "fixture"
        )

    async def guided(directory, **kwargs):
        assert directory == tmp_path and kwargs["profile"] == profile
        return config, owner, provider_path

    class Database:
        def __init__(self, config, token, organization):
            assert token == "private-pat" and organization == "selected"
            events.append("database")

        def close(self):
            events.append("database_closed")

    class Operator:
        def __init__(self, config, directory):
            self.config, self.directory = config, directory

        async def close(self):
            events.append("operator_closed")

    async def deploy(operator, provider):
        assert provider == provider_path
        events.append("deploy")

    async def run(operator, **kwargs):
        assert isinstance(kwargs["database"], Database)
        assert kwargs["owner_subject"] == owner
        assert kwargs["check"] is True
        await kwargs["deploy"]()
        return {"inference_verified": True}

    monkeypatch.setattr(onboarding, "discover_ollama", discover)
    monkeypatch.setattr(provider_cli, "guided_existing_setup", guided)
    monkeypatch.setattr(management_database, "ManagementDatabase", Database)
    monkeypatch.setattr(operator_module, "Operator", Operator)
    monkeypatch.setattr(sharing, "run_share", run)
    monkeypatch.setattr(share_cli, "deploy_selected", deploy)
    result = CliRunner().invoke(
        share,
        [
            "--state-dir",
            str(tmp_path),
            "--gateway",
            "edge",
            "--check",
        ],
    )
    assert result.exit_code == 0, result.output
    assert events == ["database", "deploy", "database_closed", "operator_closed"]
    assert "private-pat" not in result.output
