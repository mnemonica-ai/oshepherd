"""Resume local setup without duplicate identities/keys or secret output."""

import asyncio
import json
import time
from uuid import uuid4

from dotenv import dotenv_values
import httpx
import pytest

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from scripts.supabase_demo import DemoState, ORIGIN, setup


class LocalServices:
    def __init__(self, *, lose_creation=False, lose_exchange=False):
        self.users = []
        self.rpc_requests = {}
        self.creations = 0
        self.exchanges = 0
        self.lose_creation = lose_creation
        self.lose_exchange = lose_exchange
        self.worker_id = str(uuid4())

    async def __call__(self, request):
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test:latest"}]})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        if path == "/auth/v1/admin/users":
            if request.method == "GET":
                return httpx.Response(200, json={"users": self.users})
            self.creations += 1
            user = {"id": str(uuid4()), "email": json.loads(request.content)["email"]}
            self.users.append(user)
            if self.lose_creation:
                self.lose_creation = False
                raise httpx.ReadTimeout("Creation committed, acknowledgement lost")
            return httpx.Response(201, json=user)
        if path.startswith("/auth/v1/admin/users/"):
            user = next(user for user in self.users if path.endswith(user["id"]))
            return httpx.Response(200, json=user)
        if path == "/auth/v1/admin/generate_link":
            return httpx.Response(200, json={"hashed_token": "one-time-test-token"})
        if path == "/auth/v1/verify":
            self.exchanges += 1
            if self.lose_exchange:
                self.lose_exchange = False
                raise httpx.ReadTimeout("Exchange committed, session lost")
            return httpx.Response(
                200,
                json={
                    "user": self.users[-1],
                    "access_token": "worker-access",
                    "refresh_token": "worker-refresh",
                    "expires_at": int(time.time()) + 3600,
                    "expires_in": 3600,
                },
            )
        operation = path.rsplit("/", 1)[-1]
        envelope = json.loads(request.content)["p_request"]
        saved = self.rpc_requests.setdefault(operation, envelope)
        assert (
            saved == envelope
        ), "Setup must replay identical operation IDs and key material"
        result = {"cluster_id": envelope["cluster_id"]}
        if operation == "create_api_key":
            result["key_id"] = envelope["key_id"]
        if operation == "enroll_worker":
            result.update(worker_id=self.worker_id, capacity=1)
        return httpx.Response(200, json={"v": 1, "data": result})


def test_setup_recovers_committed_auth_creation_and_preserves_key_and_session(tmp_path):
    async def run():
        services = LocalServices(lose_creation=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(services)) as client:
            local = {
                "SECRET_KEY": "sb_secret_operator-test",
                "PUBLISHABLE_KEY": "public-test",
            }
            with pytest.raises(RpcError, match="local_service_unavailable"):
                await setup(client, local, model="test", directory=tmp_path)
            first = DemoState.model_validate(read_private_json(tmp_path / "state.json"))
            completed = await setup(client, local, model=None, directory=tmp_path)
            again = await setup(client, local, model=None, directory=tmp_path)
        assert services.creations == 2
        assert services.exchanges == 1
        assert completed.cluster_id == first.cluster_id == again.cluster_id
        assert (
            completed.application_key == first.application_key == again.application_key
        )
        worker_config = dotenv_values(tmp_path / "worker.env")
        assert worker_config["SUPABASE_URL"] == ORIGIN
        assert worker_config["SUPABASE_PUBLISHABLE_KEY"] == "public-test"
        assert "SUPABASE_SECRET_KEY" not in worker_config
        for filename in (
            "state.json",
            "worker-session.json",
            "worker.env",
            "edge.env",
            "client.json",
        ):
            path = tmp_path / filename
            assert path.stat().st_mode & 0o777 == 0o600
            assert "sb_secret_operator-test" not in path.read_text()
        assert completed.application_key.get_secret_value() not in repr(completed)

    asyncio.run(run())


def test_ambiguous_one_time_exchange_requires_explicit_recovery(tmp_path):
    async def run():
        services = LocalServices(lose_exchange=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(services)) as client:
            local = {
                "SECRET_KEY": "sb_secret_operator-test",
                "PUBLISHABLE_KEY": "public-test",
            }
            with pytest.raises(RpcError, match="worker_auth_ambiguous"):
                await setup(client, local, model="test", directory=tmp_path)
            with pytest.raises(RpcError, match="enrollment_exchange_ambiguous"):
                await setup(client, local, model=None, directory=tmp_path)
            assert services.exchanges == 1
            await setup(
                client, local, model=None, directory=tmp_path, recover_enrollment=True
            )
        assert services.creations == 2
        assert services.exchanges == 2

    asyncio.run(run())


def test_private_state_rejects_symlinked_ancestors_and_oversized_files(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir()
    path = directory / "state.json"
    write_private_json(path, {"safe": "value"})
    link = tmp_path / "linked"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        read_private_json(link / "state.json")
    with pytest.raises(ValueError, match="symlinks"):
        write_private_json(link / "state.json", {"unsafe": "replacement"})
    assert read_private_json(path) == {"safe": "value"}
    with pytest.raises(ValueError, match="size limit"):
        read_private_json(path, limit=2)


def test_incomplete_installation_journal_never_creates_replacement_ids(tmp_path):
    state = DemoState(model="test")
    state.save(tmp_path)
    value = read_private_json(tmp_path / "state.json")
    del value["cluster_id"]
    write_private_json(tmp_path / "state.json", value)
    with pytest.raises(ValueError, match="Incomplete local installation journal"):
        DemoState.load(tmp_path)
