"""Bounded 1/5-reader workload against an already running isolated fake worker.

Does not register/restart a worker or change Auth. Uses exact committed fixtures,
up to five concurrent requests and a 60-second request bound. Not qualification.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
import time
from uuid import uuid4

import httpx

from oshepherd.backends.supabase.operator import Installation, OperatorConfig
from oshepherd.backends.supabase.private_files import write_private_json


async def run(args):
    config = OperatorConfig.load(args.connection_file)
    if config.project_ref == "local":
        raise ValueError("hosted_connection_required")
    state = Installation.load(args.state_dir, config)
    url = config.gateway_url or config.supabase_url + "/functions/v1/ollama"
    headers = {"Authorization": "Bearer " + state.application_key.get_secret_value()}
    expected = (
        Path(__file__).resolve().parents[1]
        / "protocol/v1/fixtures/generate.legacy.ndjson"
    ).read_bytes()
    results = {"hosted_qualified": False, "workloads": []}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            inventory = await client.get(url + "/api/tags", headers=headers)
            assert inventory.status_code == 200, "fixture_worker_not_available"
            assert any(
                item.get("name") == "fixture:latest" and item.get("digest") == "a" * 64
                for item in inventory.json()["models"]
            ), "isolated_fixture_worker_required"

            async def stream():
                before = time.monotonic()
                async with client.stream(
                    "POST",
                    url + "/api/generate",
                    headers=headers,
                    json={
                        "model": "fixture",
                        "prompt": "stream-load-" + str(uuid4()),
                        "stream": True,
                    },
                ) as response:
                    if response.status_code != 200:
                        results["failed_status_code"] = response.status_code
                        body = await response.aread()
                        try:
                            code = json.loads(body).get("error")
                            if isinstance(code, str) and re.fullmatch(
                                r"[a-z][a-z0-9_]{0,63}", code
                            ):
                                results["failed_error_code"] = code
                        except (ValueError, AttributeError):
                            pass
                        raise AssertionError("stream_admission_failed")
                    reader = response.aiter_lines()
                    first = await anext(reader)
                    first_seconds = time.monotonic() - before
                    assert (
                        json.loads(first)["done"] is False
                    ), "first_record_not_nonterminal"
                    lines = [first]
                    async for line in reader:
                        lines.append(line)
                    body = ("\n".join(lines) + "\n").encode()
                    assert body == expected, "stream_fixture_mismatch"
                    return {
                        "first_record_seconds": round(first_seconds, 3),
                        "complete_seconds": round(time.monotonic() - before, 3),
                        "bytes": len(body),
                    }

            async with asyncio.timeout(150):
                for concurrency in (1, 5):
                    outcomes = await asyncio.gather(
                        *(stream() for _ in range(concurrency)), return_exceptions=True
                    )
                    measurements = [item for item in outcomes if isinstance(item, dict)]
                    failures = [
                        item for item in outcomes if isinstance(item, BaseException)
                    ]
                    if failures:
                        results["failed_workload_readers"] = concurrency
                        results["successful_readers_before_failure"] = len(measurements)
                        results["failed_readers"] = len(failures)
                        raise failures[0]
                    results["workloads"].append(
                        {
                            "concurrent_readers": concurrency,
                            "successful_exact_streams": len(measurements),
                            "first_record_max_seconds": max(
                                item["first_record_seconds"] for item in measurements
                            ),
                            "completion_max_seconds": max(
                                item["complete_seconds"] for item in measurements
                            ),
                            "total_bytes": sum(item["bytes"] for item in measurements),
                        }
                    )
            results["complete"] = True
            print("hosted_stream_load", json.dumps(results), flush=True)
    finally:
        if args.report_file:
            write_private_json(args.report_file, results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--report-file", type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
