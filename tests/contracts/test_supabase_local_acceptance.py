"""SDK wire compatibility and explicitly bound local operator configuration."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from click.testing import CliRunner
import httpx
import ollama
import pytest

from oshepherd.cli import main
from oshepherd.backends.supabase.operator import Installation, Operator, OperatorConfig
from uuid import uuid4
from oshepherd.backends.supabase.private_files import write_private_json
from oshepherd.backends.supabase.releases import (
    Database,
    database_environment,
    OperatorError,
)
from oshepherd.backends.supabase.worker import SupabaseWorker
from oshepherd.backends.supabase.gateway import create_supabase_app
from tests.contracts.conftest import CASES
from tests.contracts.test_supabase_gateway import FixtureRpc, config, KEY


def test_sdk_legacy_embeddings_reaches_redis_queue(legacy_api):
    legacy_api.task.get.return_value = {"embedding": [0.25, -0.5]}
    with ollama.Client(
        host="http://testserver", transport=legacy_api.client._transport
    ) as client:
        result = client.embeddings(model="fixture:latest", prompt="hello")
    assert result.embedding == [0.25, -0.5]
    payload = json.loads(legacy_api.delay.call_args.args[0])["payload"]
    assert payload["input"] == "hello" and "prompt" not in payload


def test_sdk_legacy_embeddings_reaches_supabase_queue():
    async def run():
        fixture = next(c for c in CASES if c["id"] == "embeddings-prompt-only")
        rpc = FixtureRpc(fixture)
        app = create_supabase_app(config(), rpc_factory=lambda: rpc)
        async with ollama.AsyncClient(
            host="http://gateway",
            transport=httpx.ASGITransport(app),
            headers={"Authorization": "Bearer " + KEY},
        ) as client:
            result = await client.embeddings(model="fixture:latest", prompt="x")
        assert result.embedding == fixture["response"]["json"]["embedding"]
        assert any(name == "submit_job" for name, _ in rpc.calls)

    asyncio.run(run())


@pytest.mark.parametrize(
    "capabilities,expected",
    [(["decision"], ["systemone"]), (["completion"], ["generate", "chat"])],
)
def test_worker_advertises_systemone_only_for_decision_models(capabilities, expected):
    async def run():
        async def metadata(operation, model=None):
            return {
                "tags": {"models": [{"name": "fixture", "digest": "a" * 64}]},
                "ps": {"models": []},
                "version": {"version": "fixture"},
                "show": {"capabilities": capabilities},
            }[operation]

        rpc = SimpleNamespace(rpc=AsyncMock())
        worker = SupabaseWorker(rpc, SimpleNamespace(metadata=metadata))
        worker.session = {"session_id": "fixture", "generation": 1}
        await worker.inventory()
        assert rpc.rpc.call_args.args[1]["models"][0]["operations"] == expected

    asyncio.run(run())


def test_configure_imports_explicit_local_ports_and_database_password(tmp_path):
    status = tmp_path / "status.json"
    output = tmp_path / "connection.json"
    write_private_json(
        status,
        {
            "API_URL": "http://127.0.0.1:56321",
            "DB_URL": "postgresql://postgres:selected%21@127.0.0.1:56322/postgres",
            "PUBLISHABLE_KEY": "sb_publishable_test",
            "SECRET_KEY": "sb_secret_test",
        },
    )
    result = CliRunner().invoke(
        main,
        ["supabase", "configure", "--local-from", str(status), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    config = OperatorConfig.load(output)
    env = Database(config).env
    assert (config.local_api_port, env["PGPORT"], env["PGUSER"], env["PGPASSWORD"]) == (
        56321,
        "56322",
        "supabase_admin",
        "selected!",
    )
    assert "selected" not in result.output
    with pytest.raises(OperatorError, match="database_target_mismatch"):
        database_environment(config.database_url.get_secret_value(), "local")


@pytest.mark.parametrize(
    "api,database",
    [
        (
            "http://remote.test:56321",
            "postgresql://postgres:x@127.0.0.1:56322/postgres",
        ),
        (
            "http://127.0.0.1:56321",
            "postgresql://postgres:x@remote.test:56322/postgres",
        ),
    ],
)
def test_local_configure_refuses_remote_targets(tmp_path, api, database):
    status, output = tmp_path / "status.json", tmp_path / "connection.json"
    write_private_json(
        status,
        {
            "API_URL": api,
            "DB_URL": database,
            "PUBLISHABLE_KEY": "sb_publishable_test",
            "SECRET_KEY": "sb_secret_test",
        },
    )
    result = CliRunner().invoke(
        main,
        ["supabase", "configure", "--local-from", str(status), "--output", str(output)],
    )
    assert result.exit_code != 0 and not output.exists()


def test_local_edge_export_uses_container_injected_connection(tmp_path):
    config = OperatorConfig(
        project_ref="local",
        supabase_url="http://127.0.0.1:56321",
        local_api_port=56321,
        local_database_port=56322,
        publishable_key="sb_publishable_test",
        secret_key="sb_secret_test",
    )

    async def run():
        operator = Operator(config, tmp_path)
        try:
            state = Installation(
                project_ref="local",
                supabase_url=config.supabase_url,
                profile="edge_free",
                owner_subject=uuid4(),
            )
            operator.export_configuration(state)
        finally:
            await operator.close()

    asyncio.run(run())
    edge = (tmp_path / "edge.env").read_text()
    assert "OSHEPHERD_PUBLIC_URL=" in edge
    assert "OSHEPHERD_SUPABASE_URL=" not in edge
    assert "OSHEPHERD_GATEWAY_KEY=" not in edge
    assert "sb_secret_test" not in edge
