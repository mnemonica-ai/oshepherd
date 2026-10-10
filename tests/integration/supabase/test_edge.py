"""Full local HTTP -> Edge -> SQL -> external worker -> Ollama lifecycle."""

import asyncio
import json
import os
from pathlib import Path
import time

from jsonschema import Draft202012Validator
import pytest

from tests.support.fake_ollama import Scenario
from .edge_harness import edge_lifecycle
from .test_worker import upstream

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "protocol/v1/fixtures"


@pytest.mark.parametrize("rpc_schema", ["oshepherd_spike_api", "oshepherd_api"])
def test_real_edge_all_routes_and_async(local_connection, request, rpc_schema):
    if rpc_schema == "oshepherd_api":
        request.getfixturevalue("production_schema")

    async def run():
        fixtures = json.loads((FIXTURES / "routes.json").read_text())
        schema = json.loads(
            (ROOT / "protocol/supabase/v1/async.schema.json").read_text()
        )
        async with edge_lifecycle(
            local_connection, app=upstream(), systemone=True, rpc_schema=rpc_schema
        ) as (
            h,
            url,
            _,
            task,
        ):
            headers = {"Authorization": "Bearer " + h.keys[0]}
            assert (await h.client.get(url + "/health")).status_code == 401
            assert (await h.client.get(url + "/health", headers=headers)).json() == {
                "status": 200
            }
            for name in (
                "generate-defaults",
                "chat-defaults",
                "embeddings-defaults",
                "systemone-text",
                "systemone-extras-always-nonstream",
                "generate-stream",
                "chat-stream",
            ):
                fixture = next(item for item in fixtures["cases"] if item["id"] == name)
                response = await h.client.post(
                    url + fixture["request"]["path"],
                    headers=headers,
                    json=fixture["request"]["json"],
                    timeout=10,
                )
                assert response.status_code == 200, (
                    name + ": " + str(response.status_code)
                )
                if "body_file" in fixture["response"]:
                    assert (
                        response.content
                        == (FIXTURES / fixture["response"]["body_file"]).read_bytes()
                    )
                elif "body" in fixture["response"]:
                    assert response.text == fixture["response"]["body"]
                else:
                    assert response.json() == fixture["response"]["json"]
                assert not task.done()
            for operation in ("tags", "ps", "version"):
                response = await h.client.get(
                    url + "/api/" + operation, headers=headers
                )
                assert response.status_code == 200
                assert operation == "version" or len(response.json()["models"]) == 1
            response = await h.client.post(
                url + "/api/show",
                headers=headers,
                json={"model": "fixture", "verbose": True},
            )
            assert response.status_code == 200 and "model_info" in response.json()
            async_headers = {**headers, "Idempotency-Key": "edge-async-test"}
            request = {
                "endpoint": "/api/generate",
                "payload": {"model": "fixture", "prompt": "hello"},
            }
            response = await h.client.post(
                url + "/jobs", headers=async_headers, json=request
            )
            assert response.status_code == 202
            accepted = response.json()
            Draft202012Validator(schema["$defs"]["accepted"]).validate(accepted)
            assert response.headers["Location"] == accepted["status_url"]
            assert accepted["status_url"].startswith(
                local_connection["API_URL"] + "/functions/v1/ollama/jobs/"
            )
            replay = await h.client.post(
                url + "/jobs", headers=async_headers, json=request
            )
            assert replay.json()["id"] == accepted["id"]
            result = await h.client.get(
                url + "/jobs/" + accepted["id"] + "/result", headers=headers
            )
            async with asyncio.timeout(10):
                while result.status_code == 202:
                    await asyncio.sleep(0.05)
                    result = await h.client.get(
                        url + "/jobs/" + accepted["id"] + "/result", headers=headers
                    )
            assert result.status_code == 200
            assert result.json()["done"] is True
            status = await h.client.get(
                url + "/jobs/" + accepted["id"], headers=headers
            )
            Draft202012Validator(schema["$defs"]["status"]).validate(status.json())
            other = await h.client.get(
                url + "/jobs/" + accepted["id"],
                headers={"Authorization": "Bearer " + h.keys[1]},
            )
            assert other.status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize("rpc_schema", ["oshepherd_spike_api", "oshepherd_api"])
def test_real_edge_large_success_requires_authorized_download(
    local_connection, request, rpc_schema
):
    if rpc_schema == "oshepherd_api":
        request.getfixturevalue("production_schema")

    async def run():
        output = {
            "model": "fixture:latest",
            "created_at": "2026-10-05T12:00:00Z",
            "response": "x" * 1200000,
            "done": True,
        }
        app = upstream({"/api/generate": Scenario.from_json({"json": output})})
        schema = json.loads(
            (ROOT / "protocol/supabase/v1/async.schema.json").read_text()
        )
        async with edge_lifecycle(local_connection, app=app, rpc_schema=rpc_schema) as (
            h,
            url,
            _,
            task,
        ):
            headers = {"Authorization": "Bearer " + h.keys[0]}
            accepted = await h.client.post(
                url + "/jobs",
                headers=headers,
                json={
                    "endpoint": "/api/generate",
                    "payload": {"model": "fixture", "prompt": "large"},
                },
            )
            assert accepted.status_code == 202
            job_url = accepted.json()["status_url"]
            async with asyncio.timeout(20):
                while True:
                    response = await h.client.get(job_url, headers=headers)
                    state = response.json()
                    if state["status"] not in ("queued", "running"):
                        break
                    assert not task.done()
                    await asyncio.sleep(0.1)
            assert state["status"] == "succeeded" and state["result"]["available"]
            result = await h.client.get(job_url + "/result", headers=headers)
            assert (
                result.status_code == 413
                and result.json()["error"] == "result_too_large"
            )
            download = await h.client.get(
                job_url + "/result?download=true", headers=headers
            )
            assert download.status_code == 200
            descriptor = download.json()
            Draft202012Validator(schema["$defs"]["download"]).validate(descriptor)
            assert descriptor["url"].startswith(
                local_connection["API_URL"] + "/storage/v1/object/sign/"
            )
            blob = await h.client.get(descriptor["url"])
            assert (
                blob.status_code == 200
                and len(blob.content) == descriptor["size_bytes"]
            )
            import hashlib

            assert hashlib.sha256(blob.content).hexdigest() == descriptor["sha256"]
            assert blob.json()["response"] == output["response"]
            denied = await h.client.get(
                job_url + "/result?download=true",
                headers={"Authorization": "Bearer " + h.keys[1]},
            )
            assert denied.status_code == 404
            assert (await h.client.get(job_url, headers=headers)).json()[
                "status"
            ] == "succeeded"

    asyncio.run(run())


def test_real_edge_first_record_before_terminal_and_disconnect(local_connection):
    async def run():
        lines = (
            (FIXTURES / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)
        )
        app = upstream(
            {
                "/api/generate": Scenario(
                    tuple(lines), content_type="application/x-ndjson", gaps_s=(2, 2)
                )
            }
        )
        async with edge_lifecycle(local_connection, app=app) as (h, url, worker, task):
            headers = {"Authorization": "Bearer " + h.keys[0]}
            started = time.monotonic()
            async with h.client.stream(
                "POST",
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": "hello", "stream": True},
            ) as response:
                assert response.status_code == 200
                first = await anext(response.aiter_lines())
                assert json.loads(first)["done"] is False
                active_job = next(iter(worker.jobs))
                assert (
                    time.monotonic() - started < 1.5
                ), "First record waited for terminal output"
            async with asyncio.timeout(10):
                while worker.jobs:
                    await asyncio.sleep(0.05)
            assert not task.done()
            from .harness import scoped_sql

            state = scoped_sql(
                h.cluster,
                "select state from oshepherd_spike.jobs where id = :'job_id'::uuid and cluster_id = :'cluster_id'::uuid;",
                job_id=active_job,
            )
            assert "cancelled" in state

    asyncio.run(run())


@pytest.mark.skipif(
    not os.environ.get("OSHEPHERD_REAL_OLLAMA_MODEL"),
    reason="Explicit installed model required for real inference smoke test",
)
@pytest.mark.parametrize("rpc_schema", ["oshepherd_spike_api", "oshepherd_api"])
def test_real_edge_local_ollama_model(local_connection, request, rpc_schema):
    if rpc_schema == "oshepherd_api":
        request.getfixturevalue("production_schema")

    async def run():
        model = os.environ["OSHEPHERD_REAL_OLLAMA_MODEL"]
        async with edge_lifecycle(
            local_connection, ollama_url="http://127.0.0.1:11434", rpc_schema=rpc_schema
        ) as (h, url, _, task):
            response = await h.client.post(
                url + "/api/generate",
                headers={"Authorization": "Bearer " + h.keys[0]},
                json={
                    "model": model,
                    "prompt": 'Return the JSON object {"ok":true}.',
                    "format": "json",
                    "stream": False,
                    "options": {"num_predict": 8, "num_ctx": 512, "temperature": 0},
                },
                timeout=140,
            )
            assert response.status_code == 200, "Real-model Edge inference failed"
            result = response.json()
            assert result["done"] is True and result["eval_count"] <= 8
            assert isinstance(result["response"], str) and result["response"]
            assert not task.done()
            print(
                f"Real Edge lifecycle: model={model}, done=true, eval_count={result['eval_count']}, output_bytes={len(response.content)}"
            )

    asyncio.run(run())
