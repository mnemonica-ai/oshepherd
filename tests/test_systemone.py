"""Decision request validation, passthrough, and Celery failure handling."""

import json
import threading
from unittest.mock import MagicMock

import httpx
import ollama
import pytest
from fastapi.testclient import TestClient

from oshepherd.api.app import setup_api_app
from oshepherd.api.config import ApiConfig
from tests.systemone_example import ticket_request


@pytest.fixture
def api():
    app = setup_api_app(
        ApiConfig(
            CELERY_BROKER_URL="memory://", CELERY_BACKEND_URL="redis://localhost:6379/0"
        )
    )
    with TestClient(app) as client:
        yield client


def test_route_queues_payload_and_preserves_extras(api, monkeypatch):
    from oshepherd.worker.tasks import exec_completion

    payload = ticket_request("tev1:4b")
    payload["keep_alive"] = "5m"
    payload["future_parameter"] = {"value": True}
    payload["questions"]["future"] = {"type": "future_type"}
    response = {"model": "tev1:4b", "answers": {}, "usage": {}}
    task = MagicMock(id="test-task")
    task.get.return_value = response
    delay = MagicMock(return_value=task)
    monkeypatch.setattr(exec_completion, "delay", delay)

    result = api.post("/v1/systemone", json=payload)

    assert result.status_code == 200
    assert result.json() == response
    assert json.loads(delay.call_args.args[0]) == {
        "type": "systemone",
        "payload": payload,
    }


@pytest.mark.parametrize(
    "state",
    [
        "Please refund this payment.",
        {"ticket": "Please refund this payment."},
        ["Please refund this payment.", {"payment_id": 42}],
    ],
    ids=["text", "object", "array"],
)
def test_official_client_preserves_supported_state_types(api, monkeypatch, state):
    from oshepherd.worker.tasks import exec_completion

    task = MagicMock(id="test-task")
    task.get.return_value = {
        "model": "tev1:4b",
        "answers": {"refund": {"type": "noul", "noul": 0.9}},
        "usage": {"input_tokens": 20, "output_tokens": 1},
    }
    delay = MagicMock(return_value=task)
    monkeypatch.setattr(exec_completion, "delay", delay)

    def forward(request):
        response = api.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"Content-Type": "application/json"},
        )
        return httpx.Response(response.status_code, json=response.json())

    with ollama.Client(
        host="http://oshepherd.test", transport=httpx.MockTransport(forward)
    ) as client:
        result = client.systemone(
            model="tev1:4b",
            state=state,
            questions={
                "refund": {"type": "noul", "instructions": "Is a refund requested?"}
            },
        )

    assert result.answers["refund"].noul == 0.9
    assert result.usage.input_tokens == 20
    assert json.loads(delay.call_args.args[0])["payload"]["state"] == state


def test_celery_result_stays_on_the_thread_that_created_it(api, monkeypatch):
    from oshepherd.worker.tasks import exec_completion

    def delay(request):
        # Celery's default backend is thread-local. AsyncResult retains the
        # backend from its creation thread, so get() must run there too.
        creation_thread = threading.get_ident()
        task = MagicMock(id="test-task")

        def get():
            assert threading.get_ident() == creation_thread
            return {"model": "tev1:4b", "answers": {}, "usage": {}}

        task.get.side_effect = get
        return task

    monkeypatch.setattr(exec_completion, "delay", delay)
    result = api.post("/v1/systemone", json=ticket_request("tev1:4b"))
    assert result.status_code == 200, result.text


def test_queue_failure_returns_json(api, monkeypatch):
    from oshepherd.worker.tasks import exec_completion

    monkeypatch.setattr(
        exec_completion,
        "delay",
        MagicMock(side_effect=ConnectionError("broker unavailable")),
    )
    response = api.post("/v1/systemone", json=ticket_request("tev1:4b"))
    assert response.status_code == 500
    assert response.json() == {
        "error": "Internal Server Error",
        "message": "error executing completion: broker unavailable",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"model": "tev1:4b", "state": None, "questions": {}},
        {"model": "tev1:4b", "state": {}, "questions": []},
    ],
)
def test_invalid_top_level_fields_do_not_queue(api, monkeypatch, payload):
    from oshepherd.worker.tasks import exec_completion

    delay = MagicMock()
    monkeypatch.setattr(exec_completion, "delay", delay)
    result = api.post("/v1/systemone", json=payload)
    assert result.status_code == 422
    delay.assert_not_called()


@pytest.mark.parametrize("raises", [False, True])
def test_route_maps_worker_failure_to_json(api, monkeypatch, raises):
    from oshepherd.worker.tasks import exec_completion

    task = MagicMock(id="test-task")
    if raises:
        task.get.side_effect = ollama.ResponseError("model not found", 404)
    else:
        task.get.return_value = {"error": {"message": "model not found"}}
    monkeypatch.setattr(exec_completion, "delay", MagicMock(return_value=task))

    response = api.post("/v1/systemone", json=ticket_request("missing"))
    assert response.status_code == 500
    assert response.json()["error"] == "Internal Server Error"
    assert "model not found" in response.json()["message"]


def test_worker_preserves_payload_and_never_streams(api, monkeypatch):
    from oshepherd.worker.tasks import exec_completion

    payload = ticket_request("tev1:4b")
    payload.update(stream=True, future_parameter={"value": True})
    client = MagicMock()
    client.__enter__.return_value = client
    expected = {"model": "tev1:4b", "answers": {}, "usage": {}}
    client._request_raw.return_value.json.return_value = expected
    monkeypatch.setattr(ollama, "Client", MagicMock(return_value=client))
    redis = MagicMock()
    monkeypatch.setattr("oshepherd.worker.tasks.RedisService", redis)

    result = exec_completion.run(json.dumps({"type": "systemone", "payload": payload}))

    assert result == expected
    client._request_raw.assert_called_once_with("POST", "/v1/systemone", json=payload)
    redis.assert_not_called()
    client.__exit__.assert_called_once()


def test_worker_logs_and_raises_upstream_error(api, monkeypatch, caplog):
    from oshepherd.worker.tasks import exec_completion

    client = MagicMock()
    client.__enter__.return_value = client
    client._request_raw.side_effect = ollama.ResponseError("model not found", 404)
    monkeypatch.setattr(ollama, "Client", MagicMock(return_value=client))

    with pytest.raises(ollama.ResponseError, match="model not found"):
        exec_completion.run(
            json.dumps({"type": "systemone", "payload": ticket_request("missing")})
        )
    assert "exec_completion failed" in caplog.text
