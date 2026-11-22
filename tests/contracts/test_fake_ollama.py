import asyncio
import json

import httpx
import ollama
import pytest

from tests.contracts.conftest import PROTOCOL
from tests.support.fake_ollama import FakeOllama, Scenario, create_app


def test_sdk_reads_exact_deterministic_upstream():
    async def run():
        upstream = FakeOllama()
        async with ollama.AsyncClient(
            host="http://fake.test", transport=httpx.ASGITransport(app=upstream)
        ) as client:
            result = await client.generate(
                model="fixture:latest", prompt="Hola 雪", stream=False
            )
            assert result.response == "Sí, 雪 🐑"
            assert result.done is True
            chunks = [
                chunk
                async for chunk in await client.chat(
                    model="fixture:latest", messages=[], stream=True
                )
            ]
            assert chunks[0].message.content == "Sí, 雪 🐑"
            assert chunks[-1].done is True
            embedding = await client.embeddings(
                model="fixture:latest", prompt="Hola 雪"
            )
            assert embedding.embedding == [0.125, -0.5, 0.0, 1.25]
            assert upstream.requests[-1]["json"]["prompt"] == "Hola 雪"
            response = await client._request_raw(
                "POST", "/v1/systemone", json={"stream": True}
            )
            assert response.json()["usage"] == {"input_tokens": 20, "output_tokens": 1}

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["generate", "chat"])
def test_exact_ndjson_transport_bytes(operation):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=FakeOllama()), base_url="http://fake.test"
        ) as client:
            response = await client.post("/api/" + operation, json={"stream": True})
            assert response.headers["content-type"] == "application/x-ndjson"
            assert (
                response.content
                == (PROTOCOL / "fixtures" / f"{operation}.upstream.ndjson").read_bytes()
            )

    asyncio.run(run())


async def collect(scenario, sleep=asyncio.sleep):
    events = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(event):
        events.append(event)

    app = FakeOllama({"/api/generate": scenario}, sleep=sleep)
    await app(
        {"type": "http", "method": "POST", "path": "/api/generate"}, receive, send
    )
    return events


def test_first_output_and_long_silence_are_controllable():
    delays = []

    async def sleep(delay):
        delays.append(delay)

    events = asyncio.run(
        collect(
            Scenario((b"first\n", b"last\n"), first_delay_s=20, gaps_s=(200,)), sleep
        )
    )
    assert delays == [20, 200]
    assert [event.get("body") for event in events[1:]] == [b"first\n", b"last\n", b""]
    assert events[-1]["more_body"] is False


def test_arbitrary_transport_boundaries_and_large_output():
    data = "雪 🐑".encode() + b"x" * (5 * 1024 * 1024)
    # Deliberately split the first codepoint. Fragments are bytes, not NDJSON records.
    events = asyncio.run(collect(Scenario((data[:1], data[1:2], data[2:]))))
    assert b"".join(event.get("body", b"") for event in events) == data


@pytest.mark.parametrize("layout", ["one-batch", "utf8-split", "large-record"])
def test_ndjson_fragments_preserve_records_and_terminal_metadata(layout):
    data = (PROTOCOL / "fixtures" / "generate.upstream.ndjson").read_bytes()
    if layout == "one-batch":
        chunks = (data,)
    elif layout == "utf8-split":
        boundary = data.index("雪".encode()) + 1
        chunks = (data[:boundary], data[boundary:])
    else:
        lines = data.splitlines(keepends=True)
        record = json.loads(lines[0])
        record["response"] = "雪" * (64 * 1024)
        data = json.dumps(record, ensure_ascii=False).encode() + b"\n" + lines[1]
        chunks = tuple(
            data[start : start + 64 * 1024] for start in range(0, len(data), 64 * 1024)
        )
    events = asyncio.run(collect(Scenario(chunks, content_type="application/x-ndjson")))
    reconstructed = b"".join(event.get("body", b"") for event in events)
    assert reconstructed == data
    records = [json.loads(line) for line in reconstructed.splitlines()]
    assert len(records) == 2
    assert records[0]["done"] is False
    assert records[-1]["done"] is True
    assert records[-1]["eval_count"] == 3


@pytest.mark.parametrize("boundary", [0, 1, 2])
def test_disconnect_does_not_emit_graceful_eof(boundary):
    events = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"{}"}

        async def send(event):
            events.append(event)

        app = FakeOllama(
            {"/api/generate": Scenario((b"one\n", b"two\n"), disconnect_after=boundary)}
        )
        await app(
            {"type": "http", "method": "POST", "path": "/api/generate"}, receive, send
        )

    with pytest.raises(ConnectionResetError, match="fixture upstream disconnect"):
        asyncio.run(run())
    assert len(events) == 1 + boundary
    assert not any(event.get("more_body") is False for event in events)


def test_stock_client_silently_accepts_missing_terminal_marker():
    async def run():
        first_line = (
            (PROTOCOL / "fixtures" / "generate.upstream.ndjson")
            .read_bytes()
            .splitlines(keepends=True)[0]
        )
        app = FakeOllama(
            {
                "/api/generate": Scenario(
                    (first_line,), content_type="application/x-ndjson"
                )
            }
        )
        async with ollama.AsyncClient(
            host="http://fake.test", transport=httpx.ASGITransport(app=app)
        ) as client:
            chunks = [
                chunk
                async for chunk in await client.generate(
                    model="fixture:latest", prompt="", stream=True
                )
            ]
            assert len(chunks) == 1
            assert chunks[-1].done is False

    asyncio.run(run())


def test_cancellation_interrupts_long_silence():
    async def run():
        waiting = asyncio.Event()

        async def sleep(delay):
            waiting.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            collect(Scenario((b"first",), first_delay_s=200), sleep)
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_load_scenario_file(tmp_path, monkeypatch):
    config = tmp_path / "scenario.json"
    config.write_text(
        json.dumps(
            {
                "/api/generate": {
                    "chunks_hex": ["e9", "9baa0a"],
                    "content_type": "application/x-ndjson",
                    "first_delay_s": 20,
                    "gaps_s": [200],
                }
            }
        )
    )
    monkeypatch.setenv("OSHEPHERD_FAKE_OLLAMA_SCENARIOS", str(config))
    scenario = create_app().overrides["/api/generate"]
    assert b"".join(scenario.chunks) == "雪\n".encode()
    assert scenario.first_delay_s == 20
    assert scenario.gaps_s == (200,)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_delay_rejected(value):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        Scenario((b"x",), first_delay_s=value)
