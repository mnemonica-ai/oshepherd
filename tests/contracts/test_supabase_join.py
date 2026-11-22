import base64
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

from click.testing import CliRunner
import httpx
import pytest
from pydantic import ValidationError

from oshepherd.backends.supabase.onboarding import (
    CODE_PREFIX,
    OnboardingError,
    WorkerInvitation,
    default_state_dir,
    discover_ollama,
    export_invitation,
)
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.sessions import WorkerSession
from oshepherd.backends.supabase.client import RpcError

join_module = importlib.import_module("oshepherd.backends.supabase.join_cli")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def invitation(**changes):
    values = dict(
        supabase_url="https://example.supabase.co",
        publishable_key="sb_publishable_examplepublickey",
        token_hash="private-one-time-challenge",
        schema="oshepherd_api",
        capacity=2,
    )
    values.update(changes)
    return WorkerInvitation(**values)


def jwt(role, ref="example"):
    segment = (
        lambda value: base64.urlsafe_b64encode(json.dumps(value).encode())
        .decode()
        .rstrip("=")
    )
    return (
        segment({"alg": "HS256"})
        + "."
        + segment({"role": role, "ref": ref})
        + ".signature"
    )


def test_invitation_roundtrip_private_and_no_admin_material(tmp_path):
    value = invitation()
    value.save(tmp_path / "invite.json")
    loaded = WorkerInvitation.load(tmp_path / "invite.json")
    assert WorkerInvitation.decode(loaded.encode()) == value
    assert os.stat(tmp_path / "invite.json").st_mode & 0o777 == 0o600
    assert set(read_private_json(tmp_path / "invite.json")) == {
        "v",
        "supabase_url",
        "publishable_key",
        "token_hash",
        "schema",
        "capacity",
    }
    assert "private-one-time-challenge" not in repr(value)
    assert "sb_publishable" not in repr(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"publishable_key": "sb_secret_administrator"},
        {"publishable_key": jwt("service_role")},
        {"publishable_key": jwt("anon", ref="other")},
        {"publishable_key": "randomvalue"},
        {"supabase_url": "http://remote.example"},
        {"supabase_url": "https://user:password@example.supabase.co"},
        {"supabase_url": "https://example.supabase.co/path"},
        {"supabase_url": "https://example.supabase.co?token=secret"},
        {"schema": "arbitrary"},
        {"v": True},
        {"v": 2},
        {"capacity": 0},
        {"capacity": True},
        {"token_hash": "bad\nvalue"},
        {"unexpected": "adminsecret"},
    ],
)
def test_invitation_rejects_admin_wrong_project_and_invalid_shape(changes):
    with pytest.raises(ValidationError):
        invitation(**changes)


def test_valid_legacy_anon_key():
    assert invitation(publishable_key=jwt("anon")).capacity == 2


@pytest.mark.parametrize(
    "code",
    [
        "secret-bad-code",
        CODE_PREFIX + "?",
        CODE_PREFIX + "a" * 40000,
        CODE_PREFIX + "eyJ2IjoyfQ",
    ],
)
def test_code_errors_are_bounded_and_redacted(code):
    with pytest.raises(OnboardingError) as error:
        WorkerInvitation.decode(code)
    assert code not in str(error.value)


def test_private_file_error_never_echoes_payload(tmp_path):
    path = tmp_path / "invite.json"
    write_private_json(
        path, {"token_hash": "sensitivevalue", "publishable_key": "sb_secret_sensitive"}
    )
    with pytest.raises(OnboardingError) as error:
        WorkerInvitation.load(path)
    assert "sensitive" not in str(error.value)
    invitation().save(path)
    path.chmod(0o644)
    with pytest.raises(OnboardingError):
        WorkerInvitation.load(path)


def test_default_state_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_state_dir() == tmp_path / "state" / "oshepherd" / "worker"
    assert default_state_dir("share") == tmp_path / "state" / "oshepherd" / "share"
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    with pytest.raises(OnboardingError):
        default_state_dir()


@pytest.mark.anyio
async def test_discovery_is_bounded_read_only_and_selects_installed_model():
    requests = []

    def response(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"models": [{"name": "first:latest"}, {"name": "second:latest"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await discover_ollama("localhost:11434", client=client)
        assert result.url == "http://localhost:11434" and result.model is None
        assert result.models == ("first:latest", "second:latest")
        assert (
            await discover_ollama(model="second:latest", client=client)
        ).model == "second:latest"
        with pytest.raises(OnboardingError, match="not installed"):
            await discover_ollama(model="missing", client=client)
    assert requests == [("GET", "/api/tags")] * 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"models": []}),
        httpx.Response(200, json={"models": [{"name": "bad\u001bvalue"}]}),
        httpx.Response(200, content=b"a" * 262145),
        httpx.Response(302, headers={"Location": "http://another.example"}),
        httpx.Response(500, text="private-provider-error"),
    ],
)
async def test_discovery_errors_do_not_expose_response(response):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        with pytest.raises(OnboardingError) as error:
            await discover_ollama(client=client)
        assert "private-provider-error" not in str(error.value)


@pytest.fixture
def fake_lifecycle(monkeypatch):
    calls = []

    async def discovery(url, model):
        from oshepherd.backends.supabase.onboarding import OllamaDiscovery

        calls.append("discover")
        return OllamaDiscovery(url, ("model:latest",), model or "model:latest")

    async def login(config, token):
        calls.append("login")
        WorkerSession(
            supabase_url=config.SUPABASE_URL,
            user_id=uuid4(),
            access_token="private-access-token",
            refresh_token="private-refresh-token",
            expires_at=9999999999,
        ).save(config.WORKER_SESSION_FILE)

    monkeypatch.setattr(join_module, "discover_ollama", discovery)
    monkeypatch.setattr(join_module, "login_worker", login)
    return calls


@pytest.mark.anyio
async def test_first_join_persists_restricted_state_and_restart_reuses_session(
    tmp_path, fake_lifecycle
):
    config = await join_module.prepare_join(tmp_path, invitation=invitation())
    original = config.WORKER_SESSION_FILE.read_bytes()
    await join_module.prepare_join(tmp_path, model="model:latest")
    assert fake_lifecycle == ["discover", "login", "discover"]
    assert config.WORKER_SESSION_FILE.read_bytes() == original
    state = (tmp_path / "join.json").read_text()
    assert "private-one-time-challenge" not in state
    assert "private-access-token" not in state
    assert "sb_secret" not in state
    assert os.stat(config.WORKER_SESSION_FILE).st_mode & 0o777 == 0o600


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"supabase_url": "https://other.supabase.co"},
        {"publishable_key": "sb_publishable_differentkey"},
        {"capacity": 1},
        {"schema": "oshepherd_spike_api"},
    ],
)
async def test_restart_rejects_changed_target_before_network(
    tmp_path, fake_lifecycle, changes
):
    await join_module.prepare_join(tmp_path, invitation=invitation())
    fake_lifecycle.clear()
    before = (tmp_path / "join.json").read_bytes()
    with pytest.raises(OnboardingError, match="differs"):
        await join_module.prepare_join(tmp_path, invitation=invitation(**changes))
    assert fake_lifecycle == []
    assert (tmp_path / "join.json").read_bytes() == before


@pytest.mark.anyio
async def test_wrong_project_session_rejected_before_discovery(
    tmp_path, fake_lifecycle
):
    await join_module.prepare_join(tmp_path, invitation=invitation())
    data = read_private_json(tmp_path / "worker-session.json")
    data["supabase_url"] = "https://other.supabase.co"
    write_private_json(tmp_path / "worker-session.json", data)
    fake_lifecycle.clear()
    with pytest.raises(ValueError, match="another project"):
        await join_module.prepare_join(tmp_path)
    assert fake_lifecycle == []


@pytest.mark.anyio
async def test_ambiguous_exchange_requires_new_code(
    tmp_path, fake_lifecycle, monkeypatch
):
    async def failed(config, token):
        raise RpcError("worker_auth_ambiguous")

    monkeypatch.setattr(join_module, "login_worker", failed)
    with pytest.raises(RpcError):
        await join_module.prepare_join(tmp_path, invitation=invitation())
    with pytest.raises(OnboardingError, match="renewed invitation"):
        await join_module.prepare_join(tmp_path, invitation=invitation())
    with pytest.raises(RpcError):
        await join_module.prepare_join(
            tmp_path, invitation=invitation(token_hash="newchallenge")
        )
    assert "newchallenge" not in (tmp_path / "join.json").read_text()


def test_cli_hides_secret_code_and_reports_readiness_only_after_registration(
    tmp_path, fake_lifecycle, monkeypatch
):
    async def worker(config, stop, ready):
        ready.set()
        import asyncio

        await asyncio.sleep(0.01)
        stop.set()

    monkeypatch.setattr(join_module, "start_worker", worker)
    value = invitation()
    result = CliRunner().invoke(
        join_module.join, ["--state-dir", str(tmp_path)], input=value.encode() + "\n"
    )
    assert result.exit_code == 0, result.output
    assert value.encode() not in result.output
    assert "private-one-time-challenge" not in result.output
    assert "Worker connected" in result.output
    assert "Inference has not been verified" in result.output


def test_cli_failed_registration_never_claims_connected(
    tmp_path, fake_lifecycle, monkeypatch
):
    async def worker(config, stop, ready):
        raise RpcError("worker_revoked")

    monkeypatch.setattr(join_module, "start_worker", worker)
    path = tmp_path / "invite.json"
    invitation().save(path)
    result = CliRunner().invoke(
        join_module.join,
        ["--state-dir", str(tmp_path / "worker"), "--invitation-file", str(path)],
    )
    assert result.exit_code == 1
    assert "worker_revoked" in result.output and "Worker connected" not in result.output


def test_export_uses_only_worker_config(tmp_path):
    from types import SimpleNamespace
    from pydantic import SecretStr

    config = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        publishable_key=SecretStr("sb_publishable_examplepublickey"),
        secret_key=SecretStr("sb_secret_private"),
    )
    challenge = tmp_path / "challenge.json"
    write_private_json(
        challenge,
        {"v": 1, "supabase_url": config.supabase_url, "token_hash": "challenge"},
    )
    result = export_invitation(config, challenge, 2, tmp_path / "invite.json")
    assert result.capacity == 2
    assert "sb_secret" not in (tmp_path / "invite.json").read_text()


def test_join_busy_is_safe_cli_error(tmp_path):
    from oshepherd.cli import main
    from oshepherd.backends.supabase.operator import installation_lock

    with installation_lock(tmp_path):
        result = CliRunner().invoke(main, ["join", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "installation_busy" in result.output
    assert "AttributeError" not in result.output


@pytest.mark.anyio
async def test_readiness_timeout_cancels_worker(monkeypatch):
    import asyncio

    stopped = asyncio.Event()

    async def worker(config, stop, ready):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(join_module, "start_worker", worker)
    with pytest.raises(OnboardingError, match="timed out"):
        await join_module.run_join(None, readiness_seconds=0.01)
    assert stopped.is_set()


@pytest.mark.anyio
async def test_premature_worker_exit_is_not_success(monkeypatch):
    async def worker(config, stop, ready):
        return

    monkeypatch.setattr(join_module, "start_worker", worker)
    with pytest.raises(OnboardingError, match="stopped before"):
        await join_module.run_join(None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes", [{"ollama_url": "http://127.0.0.1:9999"}, {"model": "changed"}]
)
async def test_restart_rejects_changed_local_config(tmp_path, fake_lifecycle, changes):
    await join_module.prepare_join(tmp_path, invitation=invitation())
    fake_lifecycle.clear()
    with pytest.raises(OnboardingError, match="differs"):
        await join_module.prepare_join(tmp_path, **changes)
    assert not fake_lifecycle


@pytest.mark.anyio
async def test_discovery_refuses_credential_url_without_request():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected network"))
    ) as client:
        with pytest.raises(OnboardingError) as error:
            await discover_ollama("http://user:secret@localhost:11434", client=client)
        assert "secret" not in str(error.value)


@pytest.mark.parametrize("missing", ["v", "schema", "capacity"])
def test_portable_input_requires_explicit_version_and_binding(tmp_path, missing):
    data = invitation().private_data()
    del data[missing]
    path = tmp_path / "incomplete.json"
    write_private_json(path, data)
    with pytest.raises(OnboardingError):
        WorkerInvitation.load(path)


def test_pending_enrollment_accepts_hidden_renewed_invitation(
    tmp_path, fake_lifecycle, monkeypatch
):
    import asyncio

    original_login = join_module.login_worker

    async def failed(config, token):
        raise RpcError("worker_auth_ambiguous")

    monkeypatch.setattr(join_module, "login_worker", failed)
    with pytest.raises(RpcError):
        asyncio.run(join_module.prepare_join(tmp_path, invitation=invitation()))
    monkeypatch.setattr(join_module, "login_worker", original_login)

    async def worker(config, stop, ready):
        stop.set()

    monkeypatch.setattr(join_module, "start_worker", worker)
    renewed = invitation(token_hash="renewed-private-code")
    result = CliRunner().invoke(
        join_module.join, ["--state-dir", str(tmp_path)], input=renewed.encode() + "\n"
    )
    assert result.exit_code == 0, result.output
    assert "Private one-time invitation code" in result.output
    assert "renewed-private-code" not in result.output
    assert renewed.encode() not in result.output


@pytest.mark.anyio
async def test_restart_rejects_same_project_other_worker_session(
    tmp_path, fake_lifecycle
):
    await join_module.prepare_join(tmp_path, invitation=invitation())
    data = read_private_json(tmp_path / "worker-session.json")
    data["user_id"] = str(uuid4())
    write_private_json(tmp_path / "worker-session.json", data)
    fake_lifecycle.clear()
    with pytest.raises(OnboardingError, match="another worker"):
        await join_module.prepare_join(tmp_path)
    assert not fake_lifecycle


@pytest.mark.anyio
async def test_restart_rejects_different_invitation_for_same_project(
    tmp_path, fake_lifecycle
):
    await join_module.prepare_join(tmp_path, invitation=invitation())
    fake_lifecycle.clear()
    with pytest.raises(OnboardingError, match="session already exists"):
        await join_module.prepare_join(
            tmp_path, invitation=invitation(token_hash="different-worker-code")
        )
    assert not fake_lifecycle


@pytest.mark.anyio
async def test_restart_recovers_only_journaled_session_save_window(
    tmp_path, fake_lifecycle
):
    import hashlib

    await join_module.prepare_join(tmp_path, invitation=invitation())
    state = read_private_json(tmp_path / "join.json")
    state["session_user_id"] = None
    state["accepted_invitation_digest"] = None
    state["attempted_token_digest"] = hashlib.sha256(
        b"private-one-time-challenge"
    ).hexdigest()
    write_private_json(tmp_path / "join.json", state)
    await join_module.prepare_join(tmp_path)
    recovered = read_private_json(tmp_path / "join.json")
    assert (
        recovered["session_user_id"]
        == read_private_json(tmp_path / "worker-session.json")["user_id"]
    )
    assert recovered["attempted_token_digest"] is None
    recovered["session_user_id"] = None
    write_private_json(tmp_path / "join.json", recovered)
    with pytest.raises(OnboardingError, match="no recorded enrollment"):
        await join_module.prepare_join(tmp_path)


def test_invitation_export_never_replaces_existing_destination(tmp_path):
    from types import SimpleNamespace
    from pydantic import SecretStr

    config = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        publishable_key=SecretStr("sb_publishable_examplepublickey"),
    )
    challenge = tmp_path / "challenge.json"
    write_private_json(
        challenge,
        {"v": 1, "supabase_url": config.supabase_url, "token_hash": "challenge"},
    )
    destination = tmp_path / "invite.json"
    destination.write_text("existing private operator data")
    with pytest.raises(OnboardingError, match="already exists"):
        export_invitation(config, challenge, 2, destination)
    assert destination.read_text() == "existing private operator data"
    assert not list(tmp_path.glob(".invitation-*"))


def test_invitation_export_preserves_destination_created_at_publication(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from pydantic import SecretStr
    from oshepherd.backends.supabase import onboarding

    config = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        publishable_key=SecretStr("sb_publishable_examplepublickey"),
    )
    challenge = tmp_path / "challenge.json"
    write_private_json(
        challenge,
        {"v": 1, "supabase_url": config.supabase_url, "token_hash": "challenge"},
    )
    destination = tmp_path / "invite.json"
    link = os.link
    calls = []

    def concurrent_link(source, target):
        calls.append((source, target))
        assert os.stat(source).st_mode & 0o777 == 0o600
        assert (
            WorkerInvitation.load(source).token_hash.get_secret_value() == "challenge"
        )
        Path(target).write_text("concurrently created private data")
        link(source, target)

    monkeypatch.setattr(onboarding.os, "link", concurrent_link)
    with pytest.raises(OnboardingError, match="already exists"):
        export_invitation(config, challenge, 2, destination)
    assert len(calls) == 1
    assert destination.read_text() == "concurrently created private data"
    assert not list(tmp_path.glob(".invitation-*"))
