"""Characterize the real Celery executor/SDK boundary without a broker or GPU."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import ollama
import pytest

from tests.contracts.conftest import CASES, PROTOCOL
from tests.support.fake_ollama import FakeOllama, Scenario


@pytest.fixture
def worker_transport(legacy_api, monkeypatch):
    from oshepherd.worker import tasks

    upstream = FakeOllama()
    original_client = ollama.Client

    def forward(request):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=upstream), base_url="http://fake.test"
            ) as client:
                return await client.request(
                    request.method, request.url.path, content=request.content
                )

        response = asyncio.run(run())
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    def client_factory():
        return original_client(
            host="http://fake.test", transport=httpx.MockTransport(forward)
        )

    with client_factory() as client:
        monkeypatch.setattr(tasks.ollama, "generate", client.generate)
        monkeypatch.setattr(tasks.ollama, "chat", client.chat)
        monkeypatch.setattr(tasks.ollama, "embeddings", client.embeddings)
        monkeypatch.setattr(tasks.ollama, "Client", client_factory)
        store = MagicMock()
        monkeypatch.setattr(tasks, "RedisService", lambda _: store)
        monkeypatch.setattr(
            tasks,
            "load_and_validate_env",
            lambda _: SimpleNamespace(CELERY_BACKEND_URL="redis://unused"),
        )
        yield SimpleNamespace(
            task=tasks.exec_completion, upstream=upstream, store=store
        )


@pytest.mark.parametrize("operation", ["generate", "chat", "embeddings", "systemone"])
def test_worker_json_matches_sdk_characterization(worker_transport, operation):
    case = next(
        case
        for case in CASES
        if case["id"]
        == operation + ("-text" if operation == "systemone" else "-defaults")
    )
    worker = worker_transport
    result = worker.task.run(json.dumps(case["normalized"]))
    assert (
        result
        == json.loads((PROTOCOL / "fixtures" / "worker.json").read_text())[operation]
    )
    request = worker.upstream.requests[-1]
    assert request["path"] == (
        "/v1/systemone" if operation == "systemone" else "/api/" + operation
    )
    if operation == "embeddings":
        assert request["json"]["prompt"] == case["normalized"]["payload"]["input"]
        assert "input" not in request["json"]
    elif operation == "systemone":
        assert request["json"] == case["normalized"]["payload"]
    worker.store.publish.assert_not_called()


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_worker_stream_serialization_matches_golden_bytes(worker_transport, operation):
    case = next(case for case in CASES if case["id"] == operation + "-stream")
    worker = worker_transport
    assert worker.task.run(json.dumps(case["normalized"])) == {
        "status": "streaming_completed"
    }
    published = b"".join(
        (call.args[1] + "\n").encode() for call in worker.store.publish.call_args_list
    )
    assert (
        published == (PROTOCOL / "fixtures" / f"{operation}.legacy.ndjson").read_bytes()
    )
    assert json.loads(worker.store.publish.call_args_list[-1].args[1])["done"] is True


def test_worker_systemone_passthrough_even_with_stream_extra(worker_transport):
    case = next(
        case for case in CASES if case["id"] == "systemone-extras-always-nonstream"
    )
    worker = worker_transport
    worker.task.run(json.dumps(case["normalized"]))
    assert worker.upstream.requests[-1]["json"] == case["normalized"]["payload"]
    worker.store.publish.assert_not_called()


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_legacy_worker_silently_accepts_incomplete_eof(worker_transport, operation):
    case = next(case for case in CASES if case["id"] == operation + "-stream")
    first_line = (
        (PROTOCOL / "fixtures" / f"{operation}.upstream.ndjson")
        .read_bytes()
        .splitlines(keepends=True)[0]
    )
    worker = worker_transport
    worker.upstream.overrides["/api/" + operation] = Scenario(
        (first_line,), content_type="application/x-ndjson"
    )
    assert worker.task.run(json.dumps(case["normalized"])) == {
        "status": "streaming_completed"
    }
    worker.store.publish.assert_called_once()
    assert json.loads(worker.store.publish.call_args.args[1])["done"] is False


@pytest.mark.parametrize("operation", ["generate", "chat", "embeddings", "systemone"])
def test_worker_upstream_failure_raises(worker_transport, operation):
    case = next(
        case
        for case in CASES
        if case["id"]
        == operation + ("-text" if operation == "systemone" else "-defaults")
    )
    path = "/v1/systemone" if operation == "systemone" else "/api/" + operation
    worker_transport.upstream.overrides[path] = Scenario(
        (b'{"error":"fixture model missing"}',), status=404
    )
    with pytest.raises(ollama.ResponseError, match="fixture model missing"):
        worker_transport.task.run(json.dumps(case["normalized"]))
    worker_transport.store.publish.assert_not_called()


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_worker_stream_error_is_published_then_raised(worker_transport, operation):
    case = next(case for case in CASES if case["id"] == operation + "-stream")
    worker = worker_transport
    worker.upstream.overrides["/api/" + operation] = Scenario(
        (b'{"error":"fixture failure"}\n',), content_type="application/x-ndjson"
    )
    with pytest.raises(ollama.ResponseError, match="fixture failure"):
        worker.task.run(json.dumps(case["normalized"]))
    worker.store.publish.assert_called_once()
    assert json.loads(worker.store.publish.call_args.args[1]) == {
        "error": "fixture failure (status code: -1)",
        "done": True,
    }
