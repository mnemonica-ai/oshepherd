"""Byte-boundary fixtures use the same executor serialization as actual workers."""

import asyncio
import json
from uuid import uuid4
from types import SimpleNamespace
from pydantic import SecretStr

import httpx
import pytest

from scripts.supabase_hosted_envelope import output_fixture, request_body
from tests.support.fake_ollama import FakeOllama
from oshepherd.common.protocol import record_bytes
from oshepherd.worker.executor import OllamaExecutor


@pytest.mark.parametrize("size", [65536, 262144, 1047552, 1048577, 5242880])
def test_request_has_exact_wire_size_and_valid_marker(size):
    marker = "envelope-" + str(uuid4())
    body = request_body(size, marker)
    assert len(body) == size
    assert json.loads(body)["prompt"].startswith(marker)


@pytest.mark.parametrize("size", [65536, 262144, 1048576, 4000000, 4000001])
@pytest.mark.parametrize("stream", [False, True])
def test_output_fixture_matches_real_executor_and_worker_bytes(size, stream):
    async def run():
        scenario, expected = output_fixture(size, stream=stream)
        app = FakeOllama({"/api/generate": scenario})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fake"
        ) as client:
            executor = OllamaExecutor("http://fake", client=client)
            if stream:
                # Large individual records beyond the worker's 1 MiB record
                # limit are outside this streaming fixture acceptance.
                executor.max_record_bytes = 5000000
                actual = b"".join(
                    [
                        record_bytes(item)
                        async for item in executor.iter_records(
                            "generate", {"model": "fixture", "stream": True}
                        )
                    ]
                )
            else:
                actual = json.dumps(
                    await executor.complete("generate", {"model": "fixture"})
                ).encode()
        assert len(expected) == size
        assert actual == expected

    asyncio.run(run())


def test_small_fixture_sizes_are_rejected():
    with pytest.raises(ValueError, match="output_fixture_too_small"):
        output_fixture(1)
    with pytest.raises(ValueError, match="request_fixture_too_small"):
        request_body(1, "envelope-" + str(uuid4()))


@pytest.mark.parametrize("state", ["queued", "running"])
def test_failure_cancels_owned_unclaimed_or_running_sync_job_without_leaking_token(
    monkeypatch, state
):
    import scripts.supabase_hosted_envelope as probe

    cluster, caller, identifier, token = (str(uuid4()) for _ in range(4))
    calls = []

    class Database:
        def __init__(self, _):
            pass

        def execute(self, sql):
            assert "cluster_id='" + cluster + "'" in sql
            assert "key_id='" + caller + "'" in sql
            return json.dumps(
                [{"id": identifier, "state": state, "consumer_token": token}]
            )

    class Client:
        async def post(self, *_, **__):
            return httpx.Response(502, json={"error": "fixture_failure"})

    class Rpc:
        async def rpc(self, name, data):
            calls.append((name, data))

    monkeypatch.setattr(probe, "Database", Database)
    results = {}
    with pytest.raises(AssertionError, match="envelope_status_request_65536"):
        asyncio.run(
            probe.check_envelope(
                Client(),
                SimpleNamespace(overrides={}),
                SimpleNamespace(rpc=Rpc()),
                SimpleNamespace(profile="edge_free"),
                SimpleNamespace(
                    cluster_id=cluster,
                    key_id=caller,
                    application_key=SecretStr("private-key"),
                ),
                "https://gateway.test",
                {},
                results,
            )
        )
    assert calls == [
        (
            "cancel_job",
            {
                "cluster_id": cluster,
                "key": "private-key",
                "job_id": identifier,
                "consumer_token": token,
                "reason": "cancelled",
            },
        )
    ]
    assert token not in json.dumps(results) and "private-key" not in json.dumps(results)
