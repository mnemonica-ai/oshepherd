"""Controllable ASGI upstream. No Redis, model downloads, or sampled prose.

Run with: python -m uvicorn tests.support.fake_ollama:create_app --factory
Configure per-path overrides using OSHEPHERD_FAKE_OLLAMA_SCENARIOS (JSON file).
This test service intentionally has no authentication; bind it to loopback.
"""

import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


@dataclass(frozen=True)
class Scenario:
    chunks: tuple[bytes, ...]
    content_type: str = "application/json"
    status: int = 200
    first_delay_s: float = 0
    gaps_s: tuple[float, ...] = ()
    # Close abruptly before chunk N; len(chunks) disconnects after the final
    # bytes but before graceful EOF. None emits a normal HTTP EOF.
    disconnect_after: int | None = None

    def __post_init__(self):
        for delay in (self.first_delay_s, *self.gaps_s):
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("delays must be finite and nonnegative")
        if self.disconnect_after is not None and not 0 <= self.disconnect_after <= len(
            self.chunks
        ):
            raise ValueError("disconnect_after must identify a chunk boundary")

    @classmethod
    def from_json(cls, data):
        options = dict(data)
        encodings = [
            key for key in ("chunks_hex", "body_utf8", "json") if key in options
        ]
        if len(encodings) != 1:
            raise ValueError("choose exactly one of chunks_hex, body_utf8, json")
        encoding = encodings[0]
        body = options.pop(encoding)
        if encoding == "chunks_hex":
            options["chunks"] = tuple(bytes.fromhex(chunk) for chunk in body)
        elif encoding == "body_utf8":
            options["chunks"] = (body.encode("utf-8"),)
        else:
            options["chunks"] = (json.dumps(body, ensure_ascii=False).encode("utf-8"),)
        options["gaps_s"] = tuple(options.get("gaps_s", ()))
        return cls(**options)


class FakeOllama:
    def __init__(self, overrides=None, *, sleep=asyncio.sleep):
        self.overrides = overrides or {}
        self.sleep = sleep
        self.requests = []
        self.responses = json.loads((FIXTURES / "upstream.json").read_text())

    def scenario(self, path, payload):
        if path in self.overrides:
            return self.overrides[path]
        operation = path.rsplit("/", 1)[-1]
        if operation not in self.responses:
            return Scenario((b'{"error":"fixture endpoint not found"}',), status=404)
        if operation in ("generate", "chat") and payload.get("stream", False):
            # Each line is independent by default; overrides can split UTF-8,
            # coalesce records, omit the done marker, or send huge records.
            data = (FIXTURES / f"{operation}.upstream.ndjson").read_bytes()
            return Scenario(
                tuple(data.splitlines(keepends=True)),
                content_type="application/x-ndjson",
            )
        return Scenario(
            (json.dumps(self.responses[operation], ensure_ascii=False).encode("utf-8"),)
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            raise ValueError("fake upstream only supports HTTP")
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if not event.get("more_body", False):
                break
        payload = json.loads(body) if body else {}
        self.requests.append(
            {"method": scope["method"], "path": scope["path"], "json": payload}
        )
        scenario = self.scenario(scope["path"], payload)
        await send(
            {
                "type": "http.response.start",
                "status": scenario.status,
                "headers": [(b"content-type", scenario.content_type.encode("ascii"))],
            }
        )
        try:
            if scenario.first_delay_s:
                await self.sleep(scenario.first_delay_s)
            for index, chunk in enumerate(scenario.chunks):
                if scenario.disconnect_after == index:
                    raise ConnectionResetError("fixture upstream disconnect")
                if index and index - 1 < len(scenario.gaps_s):
                    await self.sleep(scenario.gaps_s[index - 1])
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            if scenario.disconnect_after == len(scenario.chunks):
                raise ConnectionResetError("fixture upstream disconnect")
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            # Propagate cancellation to model a worker closing its HTTP read.
            raise


def create_app():
    filename = os.environ.get("OSHEPHERD_FAKE_OLLAMA_SCENARIOS")
    data = json.loads(Path(filename).read_text()) if filename else {}
    return FakeOllama({path: Scenario.from_json(value) for path, value in data.items()})
