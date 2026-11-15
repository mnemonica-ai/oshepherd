"""Finite hosted request/output boundaries and slow-reader/control observations.

Used by supabase_hosted_check --envelope on idle owned test installations.
Keeps application limits intact. Client RSS is never called gateway memory;
provider ceilings, remaining quota and disconnect qualification stay unmeasured.
"""

import asyncio
import hashlib
import json
import time
from uuid import UUID, uuid4

import httpx

from oshepherd.backends.supabase.releases import Database
from oshepherd.common.protocol import record_bytes
from oshepherd.worker.executor import OllamaExecutor
from tests.support.fake_ollama import Scenario

REQUEST_LIMIT = 1048576


def request_body(size, marker):
    """An exact byte-length ASCII request, including a unique SQL lookup prefix."""
    payload = {"model": "fixture", "prompt": marker, "stream": False}
    body = json.dumps(payload, separators=(",", ":")).encode()
    if size < len(body):
        raise ValueError("request_fixture_too_small")
    payload["prompt"] += "x" * (size - len(body))
    return json.dumps(payload, separators=(",", ":")).encode()


def output_fixture(size, *, stream=False):
    """Exact normalized legacy bytes; upstream bytes may be smaller."""
    record = {"model": "fixture:latest", "response": "", "done": True}
    normalized = OllamaExecutor.normalized_record("generate", record)
    serialize = record_bytes if stream else lambda value: json.dumps(value).encode()
    base = serialize(normalized)
    padding = size - len(base)
    if padding < 0:
        raise ValueError("output_fixture_too_small")
    record["response"] = "x" * padding
    normalized["response"] = record["response"]
    expected = serialize(normalized)
    if stream:
        raw = json.dumps(record, separators=(",", ":")).encode() + b"\n"
        return Scenario((raw,), content_type="application/x-ndjson"), expected
    return Scenario.from_json({"json": record}), expected


async def check_envelope(client, app, worker, config, state, url, headers, results):
    cluster = str(UUID(str(state.cluster_id)))
    caller = str(UUID(str(state.key_id)))
    db = Database(config)
    results.update(
        envelope_cases=[],
        hosted_qualified=False,
        gateway_memory_measured=False,
        provider_ceiling_measured=False,
        remaining_provider_quota_measured=False,
    )
    markers = []
    failures = []

    async def jobs(marker):
        # All SQL interpolation is UUID-validated, never payload/provider text.
        UUID(marker.removeprefix("envelope-"))
        value = await asyncio.to_thread(
            db.execute,
            "select coalesce(json_agg(json_build_object('id',id,'state',state,"
            "'attempts',attempts,'output_bytes',output_bytes,'error',error,"
            "'consumer_token',consumer_token)),'[]'::json) "
            f"from oshepherd.jobs where cluster_id='{cluster}' and key_id='{caller}' "
            f"and left(request #>> '{{payload,prompt}}',45)='{marker}';",
        )
        return json.loads(value)

    async def recorded(marker):
        async with asyncio.timeout(20):
            while True:
                found = await jobs(marker)
                if found and found[0]["state"] not in ("queued", "running"):
                    assert len(found) == 1, "envelope_duplicate_job"
                    return found[0]
                await asyncio.sleep(0.5)

    async def request(case, scenario, expected=None, *, size=None, expected_status=200):
        marker = "envelope-" + str(uuid4())
        markers.append(marker)
        app.overrides["/api/generate"] = scenario
        start = time.monotonic()
        try:
            response = await client.post(
                url + "/api/generate",
                headers={**headers, "Content-Type": "application/json"},
                content=(
                    request_body(size, marker)
                    if size
                    else json.dumps(
                        {"model": "fixture", "prompt": marker, "stream": False}
                    ).encode()
                ),
                timeout=60,
            )
        except httpx.HTTPError:
            results["envelope_cases"].append(
                {
                    "case": case,
                    "transport_failed": True,
                    "seconds": round(time.monotonic() - start, 3),
                }
            )
            failures.append(case)
            return
        item = {
            "case": case,
            "status": response.status_code,
            "seconds": round(time.monotonic() - start, 3),
            "response_bytes": len(response.content),
        }
        if size:
            item["request_bytes"] = size
        results["envelope_cases"].append(item)
        assert response.status_code == expected_status, "envelope_status_" + case
        if expected_status == 200:
            assert response.content == expected, "envelope_bytes_" + case
            item["sha256"] = hashlib.sha256(response.content).hexdigest()
            data = await recorded(marker)
            assert data["state"] == "succeeded", "envelope_sql_not_succeeded"
        elif size:
            assert not await jobs(marker), "oversized_request_admitted"
            item["rejected_before_admission"] = True
            try:
                item["application_error"] = (
                    response.json().get("error") == "request_too_large"
                )
            except (ValueError, AttributeError):
                item["application_error"] = False
        else:
            item["error"] = response.json().get("error")
            assert item["error"] in (
                "result_too_large",
                "output_limit_exceeded",
                "output_too_large",
            )
            data = await recorded(marker)
            item["sql_state"] = data["state"]
            assert data["state"] in ("succeeded", "failed")
            assert data["attempts"] == 1, "oversized_output_retried"
        print("hosted_envelope", json.dumps(item), flush=True)

    try:
        async with asyncio.timeout(600):
            scenario, expected = output_fixture(1024)
            for size in (65536, 262144, REQUEST_LIMIT - 1024):
                await request("request_" + str(size), scenario, expected, size=size)
            for size in (REQUEST_LIMIT + 1, 5 * REQUEST_LIMIT):
                await request(
                    "request_" + str(size), scenario, size=size, expected_status=413
                )
            response_limit = REQUEST_LIMIT
            # Includes inline->Storage transition and the exact response budget.
            for size in (65536, 262144, response_limit):
                scenario, expected = output_fixture(size)
                await request("json_" + str(size), scenario, expected)
            scenario, _ = output_fixture(response_limit + 1)
            await request(
                "json_over_limit",
                scenario,
                expected_status=413 if config.profile == "edge_free" else 500,
            )

            # Many bounded records cross read pages/chunks; client stalls for five
            # seconds while metadata remains independently available.
            scenario, line = output_fixture(32768, stream=True)
            record = json.loads(scenario.chunks[0])
            record["done"] = False
            raw = json.dumps(record, separators=(",", ":")).encode() + b"\n"
            expected_line = record_bytes(
                OllamaExecutor.normalized_record("generate", record)
            )
            total = 24
            app.overrides["/api/generate"] = Scenario(
                (raw,) * total + scenario.chunks,
                content_type="application/x-ndjson",
                gaps_s=(0.02,) * total,
            )
            marker = "envelope-" + str(uuid4())
            markers.append(marker)
            start = time.monotonic()
            async with client.stream(
                "POST",
                url + "/api/generate",
                headers=headers,
                json={"model": "fixture", "prompt": marker, "stream": True},
                timeout=90,
            ) as response:
                assert response.status_code == 200, "envelope_stream_admission"
                reader = response.aiter_bytes()
                first = await anext(reader)
                await asyncio.sleep(5)
                before = time.monotonic()
                metadata = await client.get(url + "/api/tags", headers=headers)
                assert metadata.status_code == 200, "envelope_control_stalled"
                control_seconds = time.monotonic() - before
                body = bytearray(first)
                async for chunk in reader:
                    body.extend(chunk)
                    assert len(body) <= response_limit, "envelope_stream_unbounded"
                    await asyncio.sleep(0.02)
            assert bytes(body) == expected_line * total + line, "envelope_stream_bytes"
            data = await recorded(marker)
            assert data["state"] == "succeeded" and data["attempts"] == 1
            results["slow_reader"] = {
                "bytes": len(body),
                "paused_seconds": 5,
                "metadata_seconds": round(control_seconds, 3),
                "seconds": round(time.monotonic() - start, 3),
                "sha256": hashlib.sha256(body).hexdigest(),
                "server_backpressure_proven": False,
            }
            results["failed_cases"] = failures
            results["envelope_passed"] = not failures
            assert not failures, "envelope_transport_failure"
    finally:
        # The probe is also the API caller. Use its exact consumer token to
        # cancel owned queued OR running jobs; the async HTTP API excludes sync
        # jobs, and a worker-only failure would leave an unclaimed job behind.
        for marker in markers:
            for data in await jobs(marker):
                identifier = str(UUID(data["id"]))
                if data["state"] in ("queued", "running"):
                    await worker.rpc.rpc(
                        "cancel_job",
                        {
                            "cluster_id": cluster,
                            "key": state.application_key.get_secret_value(),
                            "job_id": identifier,
                            "consumer_token": (
                                str(UUID(data["consumer_token"]))
                                if data["consumer_token"]
                                else None
                            ),
                            "reason": "cancelled",
                        },
                    )
        app.overrides.pop("/api/generate", None)
