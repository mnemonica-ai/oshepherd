import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from pydantic import BaseModel

from oshepherd.common.lib import load_and_validate_env
from oshepherd.common.protocol import normalize_request, record_bytes, utf8_fragments
from oshepherd.worker.executor import (
    IncompleteOutputError,
    OllamaExecutor,
    OutputTooLargeError,
)
from tests.contracts.conftest import CASES, PROTOCOL
from tests.support.fake_ollama import FakeOllama, Scenario


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if "normalized" in case],
    ids=lambda case: case["id"],
)
def test_shared_normalizer_matches_fixtures(case):
    assert (
        normalize_request(case["normalized"]["type"], case["request"]["json"])
        == case["normalized"]
    )


@pytest.mark.parametrize("operation", ["generate", "chat", "embeddings", "systemone"])
def test_async_executor_matches_worker_json(operation):
    async def run():
        case = next(
            case
            for case in CASES
            if case["id"]
            == operation + ("-text" if operation == "systemone" else "-defaults")
        )
        app = FakeOllama()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor("http://fake.test", client=client)
            result = await executor.complete(operation, case["normalized"]["payload"])
            assert (
                result
                == json.loads((PROTOCOL / "fixtures" / "worker.json").read_text())[
                    operation
                ]
            )
            if operation == "embeddings":
                assert app.requests[-1]["json"]["prompt"] == "Hola 雪"
                assert "input" not in app.requests[-1]["json"]

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_async_executor_matches_worker_ndjson(operation):
    async def run():
        case = next(case for case in CASES if case["id"] == operation + "-stream")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=FakeOllama()), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor("http://fake.test", client=client)
            records = [
                record
                async for record in executor.iter_records(
                    operation, case["normalized"]["payload"]
                )
            ]
            assert (
                b"".join(record_bytes(record) for record in records)
                == (PROTOCOL / "fixtures" / f"{operation}.legacy.ndjson").read_bytes()
            )

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_new_executor_rejects_incomplete_output(stream):
    async def run():
        case = next(case for case in CASES if case["id"] == "generate-stream")
        record = case["records"][0]
        content = json.dumps(record).encode() + (b"\n" if stream else b"")
        app = FakeOllama({"/api/generate": Scenario((content,))})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor("http://fake.test", client=client)
            with pytest.raises(IncompleteOutputError):
                if stream:
                    async for _ in executor.iter_records(
                        "generate", case["normalized"]["payload"]
                    ):
                        pass
                else:
                    await executor.complete(
                        "generate", {**case["normalized"]["payload"], "stream": False}
                    )

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_output_size_is_enforced_before_success(stream):
    async def run():
        app = FakeOllama()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor(
                "http://fake.test", client=client, max_output_bytes=100
            )
            payload = {"model": "fixture:latest", "prompt": "", "stream": stream}
            with pytest.raises(OutputTooLargeError):
                if stream:
                    async for _ in executor.iter_records("generate", payload):
                        pass
                else:
                    await executor.complete("generate", payload)

    asyncio.run(run())


def test_one_oversized_record_cannot_accumulate_unboundedly():
    async def run():
        app = FakeOllama({"/api/generate": Scenario((b"x" * 1000,))})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor(
                "http://fake.test", client=client, max_record_bytes=100
            )
            with pytest.raises(OutputTooLargeError):
                async for _ in executor.iter_records(
                    "generate",
                    {"model": "fixture:latest", "prompt": "", "stream": True},
                ):
                    pass

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_async_jobs_reject_streaming_before_admission(operation):
    with pytest.raises(ValueError, match="async_stream_not_supported"):
        normalize_request(
            operation,
            {"model": "fixture:latest", "prompt": "", "stream": True},
            asynchronous=True,
        )
    assert (
        normalize_request(
            operation, {"model": "fixture:latest", "prompt": ""}, asynchronous=True
        )["payload"]["stream"]
        is False
    )


def test_async_systemone_preserves_ignored_stream_extra():
    payload = {"model": "fixture:latest", "state": {}, "questions": {}, "stream": True}
    assert (
        normalize_request("systemone", payload, asynchronous=True)["payload"] == payload
    )


@pytest.mark.parametrize("limit", [4, 7, 64 * 1024])
def test_fragments_preserve_unicode_and_byte_bounds(limit):
    source = ("a雪🐑" * 10000).encode()
    fragments = list(utf8_fragments(source, limit))
    assert b"".join(fragments) == source
    assert all(len(part) <= limit and part.decode() for part in fragments)


def test_null_stream_sends_explicit_nonstream_upstream():
    async def run():
        upstream = FakeOllama()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=upstream), base_url="http://fake.test"
        ) as client:
            executor = OllamaExecutor("http://fake.test", client=client)
            await executor.complete(
                "generate", {"model": "fixture:latest", "prompt": "", "stream": None}
            )
            assert upstream.requests[-1]["json"]["stream"] is False

    asyncio.run(run())


def test_first_record_is_delivered_before_the_next_upstream_record():
    async def run():
        release = asyncio.Event()
        closed = asyncio.Event()
        lines = (
            (PROTOCOL / "fixtures" / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)
        )

        class ControlledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield lines[0]
                await release.wait()
                yield lines[1]

            async def aclose(self):
                closed.set()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=ControlledStream())
            ),
            base_url="http://fake.test",
        ) as client:
            executor = OllamaExecutor("http://fake.test", client=client)
            records = executor.iter_records(
                "generate", {"model": "fixture:latest", "prompt": "", "stream": True}
            )
            first = await asyncio.wait_for(anext(records), timeout=1)
            assert first["done"] is False
            assert not release.is_set()
            await records.aclose()
            assert closed.is_set()

    asyncio.run(run())


def test_inherited_config_fields_are_loaded(monkeypatch):
    class Parent(BaseModel):
        OSHEPHERD_TEST_PARENT: str

    class Child(Parent):
        OSHEPHERD_TEST_CHILD: int

    monkeypatch.setenv("OSHEPHERD_TEST_PARENT", "inherited")
    monkeypatch.setenv("OSHEPHERD_TEST_CHILD", "7")
    config = load_and_validate_env(Child)
    assert config.OSHEPHERD_TEST_PARENT == "inherited"
    assert config.OSHEPHERD_TEST_CHILD == 7


def test_cli_and_executor_import_without_redis_or_celery():
    code = "import oshepherd.cli, oshepherd.worker.executor; import sys; assert 'celery' not in sys.modules; assert 'redis' not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CELERY_BROKER_URL": "invalid",
            "CELERY_BACKEND_URL": "invalid",
        },
    )
    assert result.returncode == 0, result.stderr
