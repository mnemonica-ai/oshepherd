import json
import os
import stat
from uuid import uuid4

from click.testing import CliRunner
import httpx
import pytest

from oshepherd.backends.supabase import operator
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.cli import main


class KeyBackend:
    def __init__(self, state):
        self.state = state
        self.calls = []
        self.receipts = {}
        self.keys = {str(state.key_id): state.application_key.get_secret_value()}
        self.revoked = set()
        self.enabled = True
        self.interrupt = None
        self.echo_error = None
        self.before_create = lambda: None

    def handle(self, request):
        rpc = request.url.path.split("/")[-1]
        data = json.loads(request.content)["p_request"]
        self.calls.append((rpc, data))
        cluster = str(self.state.cluster_id)
        assert data["cluster_id"] == cluster
        if rpc == "installation_info":
            response = {
                "cluster_id": cluster,
                "owner_subject": str(self.state.owner_subject),
                "enabled": self.enabled,
            }
        elif rpc == "backend_info":
            assert self.enabled
            assert data["key"] in {
                key for key_id, key in self.keys.items() if key_id not in self.revoked
            }
            response = {"cluster_id": cluster}
        else:
            key_id = data["key_id"]
            operation = data["operation_id"]
            if operation in self.receipts:
                _, previous, response = self.receipts[operation]
                assert previous == data
            else:
                if rpc == "create_api_key":
                    self.before_create()
                    assert key_id not in self.keys
                    self.keys[key_id] = data["raw_key"]
                    response = {"key_id": key_id, "cluster_id": cluster}
                else:
                    assert rpc == "revoke_api_key"
                    assert key_id in self.keys
                    self.revoked.add(key_id)
                    response = {"key_id": key_id, "revoked": True}
                self.receipts[operation] = rpc, data, response
        if rpc == self.interrupt:
            raise httpx.ReadError("ambiguous response", request=request)
        if rpc == self.echo_error:
            return httpx.Response(422, json={"message": data["raw_key"]})
        return httpx.Response(200, json={"v": 1, "data": response})

    def requests(self, rpc_name):
        return [data for rpc, data in self.calls if rpc == rpc_name]


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    directory = tmp_path / "xdg/oshepherd/share"
    settings = {
        "project_ref": "abcdefghijklmnopqrst",
        "supabase_url": "https://abcdefghijklmnopqrst.supabase.co",
        "publishable_key": "sb_publishable_fixturekey",
        "secret_key": "sb_secret_fixturekey",
    }
    config = operator.OperatorConfig(**settings)
    write_private_json(directory / "connection.json", settings)
    state = operator.Installation(
        project_ref=config.project_ref,
        supabase_url=config.supabase_url,
        profile=config.profile,
        stage="configured",
        owner_subject=uuid4(),
    )
    state.save(directory)
    write_private_json(
        directory / "client.json",
        {
            "v": 1,
            "gateway_url": config.supabase_url + "/functions/v1/ollama",
            "application_key": state.application_key.get_secret_value(),
        },
    )
    backend = KeyBackend(state)
    original = operator.Operator

    async def no_sleep(_):
        pass

    def connect(config, directory):
        client = httpx.AsyncClient(transport=httpx.MockTransport(backend.handle))
        instance = original(config, directory, client=client)
        instance.owns_client = True
        instance.rpc.sleep = no_sleep
        return instance

    monkeypatch.setattr(operator, "Operator", connect)
    return directory, config, state, backend


def cli(*args, code=0):
    result = CliRunner().invoke(main, args)
    assert result.exit_code == code, result.output or result.exception
    return json.loads(result.output) if code == 0 else result.output


def test_named_clients_and_main_rotation_are_independent(configured, tmp_path):
    directory, config, state, backend = configured
    main_file = directory / "client.json"
    main_bundle = main_file.read_bytes()
    assert cli("client", "list") == []
    assert backend.calls == []
    assert not (directory / "clients").exists()
    assert not (directory / "operator.lock").exists()

    output_parent = tmp_path / "exports"
    output_parent.mkdir(mode=0o755)
    first_file = output_parent / "remote-client.json"
    second_file = output_parent / "second.json"
    first = cli("client", "create", "--name", "remote-script", "--output", first_file)
    second = cli("client", "create", "--name", "second", "--output", second_file)
    assert first == {
        "name": "remote-script",
        "key_id": first["key_id"],
        "status": "active",
        "client_file": str(first_file),
    }
    first_bundle = read_private_json(first_file)
    assert first_bundle == {
        "v": 1,
        "application_key": backend.keys[first["key_id"]],
        "gateway_url": config.supabase_url + "/functions/v1/ollama",
    }
    assert stat.S_IMODE(first_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(output_parent.stat().st_mode) == 0o755
    journal = directory / "clients/remote-script.json"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert stat.S_IMODE(journal.parent.stat().st_mode) == 0o700
    assert main_file.read_bytes() == main_bundle
    assert len(set(backend.keys.values())) == 3

    # Retry the same output without replacing it. Re-export to a fresh path.
    inode = first_file.stat().st_ino
    assert (
        cli("client", "create", "--name", "remote-script", "--output", first_file)
        == first
    )
    assert first_file.stat().st_ino == inode
    replacement = output_parent / "replacement.json"
    cli("client", "create", "--name", "remote-script", "--output", replacement)
    assert read_private_json(replacement) == first_bundle
    assert len(backend.requests("create_api_key")) == 2
    calls = backend.calls.copy()
    assert cli("client", "list") == [
        {k: first[k] for k in ("name", "key_id", "status")},
        {k: second[k] for k in ("name", "key_id", "status")},
    ]
    assert backend.calls == calls

    rotated = cli(
        "supabase",
        "rotate-key",
        "--state-dir",
        directory,
        "--connection-file",
        directory / "connection.json",
    )
    assert backend.revoked == {str(state.key_id)}
    assert (
        read_private_json(main_file)["application_key"]
        == backend.keys[rotated["key_id"]]
    )
    revoked = cli("client", "revoke", "--name", "remote-script")
    assert revoked == {
        "name": "remote-script",
        "key_id": first["key_id"],
        "status": "revoked",
    }
    assert backend.revoked == {str(state.key_id), first["key_id"]}
    assert read_private_json(journal)["raw_key"] is None
    assert second["key_id"] not in backend.revoked
    assert rotated["key_id"] not in backend.revoked
    calls = backend.calls.copy()
    assert cli("client", "revoke", "--name", "remote-script") == revoked
    assert "reserved" in cli(
        "client", "create", "--name", "remote-script", "--output", first_file, code=1
    )
    assert backend.calls == calls


@pytest.mark.parametrize("resume_with_revoke", [False, True])
def test_lost_create_and_revoke_responses_replay_saved_operations(
    configured, tmp_path, resume_with_revoke
):
    directory, _, _, backend = configured
    output = tmp_path / "remote-client.json"
    journal = directory / "clients/remote-script.json"
    create = ("client", "create", "--name", "remote-script", "--output", output)
    revoke = ("client", "revoke", "--name", "remote-script")

    def check_creation_journal():
        record = read_private_json(journal)
        request = backend.requests("create_api_key")[-1]
        assert record["key_id"] == request["key_id"]
        assert record["create_operation"] == request["operation_id"]
        assert record["raw_key"] == request["raw_key"]

    backend.before_create = check_creation_journal
    backend.interrupt = "create_api_key"
    error = cli(*create, code=1)
    assert "Retry with the same name" in error
    assert not output.exists()
    assert cli("client", "list")[0]["status"] == "creating"

    backend.interrupt = None
    if not resume_with_revoke:
        assert cli(*create)["status"] == "active"
    backend.interrupt = "revoke_api_key"
    cli(*revoke, code=1)
    assert cli("client", "list")[0]["status"] == "revoking"
    assert (
        read_private_json(journal)["revoke_operation"]
        == backend.requests("revoke_api_key")[-1]["operation_id"]
    )
    assert "reserved" in cli(*create, code=1)
    backend.interrupt = None
    assert cli(*revoke)["status"] == "revoked"

    for rpc in ("create_api_key", "revoke_api_key"):
        requests = backend.requests(rpc)
        assert len(requests) == 5
        assert requests.count(requests[0]) == len(requests)
    assert len(backend.keys) == 2
    assert len(backend.revoked) == 1


def test_output_race_and_backend_errors_keep_secrets_private(configured, tmp_path):
    directory, _, _, backend = configured
    output = tmp_path / "remote-client.json"
    create = ("client", "create", "--name", "remote-script", "--output", output)
    backend.echo_error = "create_api_key"
    error = cli(*create, code=1)
    request = backend.requests("create_api_key")[0]
    assert request["raw_key"] not in error
    assert "create_api_key" in error
    assert not output.exists()
    backend.echo_error = None

    race_file = tmp_path / "collision.json"
    backend.before_create = lambda: race_file.write_text("keep this")
    assert "already exists" in cli(
        "client", "create", "--name", "race", "--output", race_file, code=1
    )
    assert race_file.read_text() == "keep this"
    assert cli(*create)["key_id"] == request["key_id"]

    # An existing hardlink or symlink to an operator file is never overwritten.
    connection = directory / "connection.json"
    original = connection.read_bytes()
    output.unlink()
    os.link(connection, output)
    assert "already exists" in cli(*create, code=1)
    output.unlink()
    output.symlink_to(connection)
    assert "already exists" in cli(*create, code=1)
    assert connection.read_bytes() == original


def test_selected_target_and_output_are_validated_before_mutation(configured, tmp_path):
    directory, config, state, backend = configured
    output = tmp_path / "remote-client.json"
    create = ("client", "create", "--name", "remote-script", "--output", output)
    assert "Client name" in cli(
        "client", "create", "--name", "../remote-script", "--output", output, code=1
    )
    assert "existing client output directory" in cli(
        "client",
        "create",
        "--name",
        "remote-script",
        "--output",
        tmp_path / "missing" / "remote-client.json",
        code=1,
    )
    assert "outside the client registry" in cli(
        "client",
        "create",
        "--state-dir",
        directory / "../share",
        "--name",
        "remote-script",
        "--output",
        directory / "clients/remote-script.json",
        code=1,
    )
    alien_config = tmp_path / "other-connection.json"
    write_private_json(
        alien_config,
        {
            "project_ref": "local",
            "supabase_url": "http://127.0.0.1:55321",
            "secret_key": "sb_secret_fixturekey",
            "publishable_key": "sb_publishable_fixturekey",
        },
    )
    assert "installation_target_mismatch" in cli(
        *create, "--connection-file", alien_config, code=1
    )
    state.stage = "disabled"
    state.save(directory)
    assert "installation_disabled" in cli(*create, code=1)
    assert backend.calls == []
    state.stage = "configured"
    state.save(directory)
    backend.enabled = False
    assert "installation_disabled" in cli(*create, code=1)
    assert not backend.requests("create_api_key")
    assert not output.exists()

    # A journal copied from another installation cannot cause cross-target RPCs.
    backend.enabled = True
    cli(*create)
    replacement = operator.Installation.load(directory, config)
    replacement.cluster_id = uuid4()
    replacement.save(directory)
    calls = backend.calls.copy()
    assert "client_record_target_mismatch" in cli("client", "list", code=1)
    assert "client_record_target_mismatch" in cli(
        "client", "revoke", "--name", "remote-script", code=1
    )
    assert backend.calls == calls


def test_explicit_local_state_dir_and_missing_installation(configured, tmp_path):
    directory, _, state, backend = configured
    local = tmp_path / "local/operator"
    state.project_ref = "local"
    state.supabase_url = "http://127.0.0.1:55321"
    state.save(local)
    write_private_json(
        local / "connection.json",
        read_private_json(directory / "connection.json")
        | {"project_ref": state.project_ref, "supabase_url": state.supabase_url},
    )
    missing = tmp_path / "missing"
    assert "Installation state is missing" in cli(
        "client", "list", "--state-dir", missing, code=1
    )
    assert not missing.exists()
    assert cli("client", "list", "--state-dir", local) == []
    assert not backend.calls
    output = tmp_path / "local-client.json"
    assert (
        cli(
            "client",
            "create",
            "--state-dir",
            local,
            "--name",
            "my-local-app",
            "--output",
            output,
        )["status"]
        == "active"
    )
    assert read_private_json(output)["gateway_url"] == (
        "http://127.0.0.1:55321/functions/v1/ollama"
    )
