"""Golden fixtures describe observed Redis behavior, including known defects."""

import json
from unittest.mock import MagicMock

import pytest

from tests.contracts.conftest import CASES, PROTOCOL, set_workers


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_route_fixture(legacy_api, monkeypatch, case):
    api = legacy_api
    if "workers" in case:
        set_workers(api, case["workers"])
    api.task.get.return_value = case.get("worker_result", {})
    if "failure" in case:
        failure = case["failure"]
        if failure["at"] == "metadata":
            target = MagicMock()
            monkeypatch.setattr(api.app.network_data, failure["method"], target)
        else:
            target = api.delay if failure["at"] == "submit" else api.task.get
        target.side_effect = RuntimeError(case["failure"]["message"])

    if "records" in case:
        store = MagicMock()
        if "stream_failure" in case:
            store.subscribe_to_channel.side_effect = RuntimeError(
                case["stream_failure"]
            )
        else:
            store.subscribe_to_channel.return_value = iter(case["records"])
        for module in ("generate", "chat"):
            monkeypatch.setattr(
                f"oshepherd.api.{module}.routes.RedisService", lambda _: store
            )

    request = case["request"]
    arguments = {"follow_redirects": False}
    if "json" in request:
        arguments["json"] = request["json"]
    if "raw" in request:
        arguments.update(
            content=request["raw"], headers={"Content-Type": "application/json"}
        )
    response = api.client.request(request["method"], request["path"], **arguments)
    expected = case["response"]
    assert response.status_code == expected["status"]
    if "content_type" in expected:
        assert response.headers["content-type"] == expected["content_type"]
    if "json" in expected:
        assert response.json() == expected["json"]
    if "body" in expected:
        assert response.text == expected["body"]
    if "body_file" in expected:
        assert (
            response.content
            == (PROTOCOL / "fixtures" / expected["body_file"]).read_bytes()
        )
    if "validation_locations" in expected:
        assert sorted(item["loc"] for item in response.json()["detail"]) == sorted(
            expected["validation_locations"]
        )
    if "location" in expected:
        assert response.headers["location"].endswith(expected["location"])

    if "normalized" in case:
        api.delay.assert_called_once()
        assert json.loads(api.delay.call_args.args[0]) == case["normalized"]
    elif case.get("queued", False):
        api.delay.assert_called_once()
    else:
        api.delay.assert_not_called()


def test_all_nine_routes_have_success_and_error_or_empty_fixtures():
    for route in range(1, 10):
        group = [case for case in CASES if case["contract"] == f"C{route:02}"]
        assert any(case["response"]["status"] == 200 for case in group)
        if route != 9:
            assert len(group) >= 2


@pytest.mark.parametrize(
    "path",
    ["/api/generate", "/api/chat", "/api/embeddings", "/v1/systemone", "/api/show"],
)
def test_trailing_slash_post_preserves_body(legacy_api, path):
    case = next(
        case
        for case in CASES
        if case["request"]["path"] == path and case["response"]["status"] == 200
    )
    if "workers" in case:
        set_workers(legacy_api, case["workers"])
    legacy_api.task.get.return_value = case.get("worker_result", {})
    response = legacy_api.client.post(path + "/", json=case["request"]["json"])
    assert [hop.status_code for hop in response.history] == [307]
    assert response.status_code == 200
    if "normalized" in case:
        assert json.loads(legacy_api.delay.call_args.args[0]) == case["normalized"]


@pytest.mark.parametrize("path", ["/api/tags", "/api/ps", "/api/version", "/health"])
def test_trailing_slash_get(legacy_api, path):
    set_workers(legacy_api, [])
    response = legacy_api.client.get(path + "/")
    assert [hop.status_code for hop in response.history] == [307]
    assert response.status_code == 200
