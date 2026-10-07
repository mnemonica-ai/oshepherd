# Supabase implementation experiments

Experimental harnesses and redacted evidence live here. Each report distinguishes
service-free characterization, local Supabase integration and hosted measurements.
Passing one does not establish another.

- [Step 0 local characterization](step-0-local-characterization.md): route and
  worker fixtures plus controllable fake Ollama, October 4, 2026.
- [Local Edge lifecycle](local-edge-lifecycle.md): real SQL/Auth/Edge/external
  worker and local-model verification, including the failing disconnect gate.
- [Local maintenance](local-maintenance.md): durable deletion recovery, orphan
  isolation, bounded SQL purge and actual database Cron, October 6, 2026.

## Controllable Ollama upstream

From the repository root:

```sh
uv run --locked uvicorn tests.support.fake_ollama:create_app --factory --host 127.0.0.1 --port 11435
```

This is a development-only service with fixed, deterministic output, no model
files and no auth. It implements all inference and metadata fixture paths.
Bind to loopback on an external test worker, and point the spike worker
at `http://127.0.0.1:11435`. Run from the checkout; tests are excluded from the
released Python package.

For overrides, create a JSON file mapping upstream paths to scenarios and set
`OSHEPHERD_FAKE_OLLAMA_SCENARIOS` to its filename. Each path must specify exactly
one of `json`, `body_utf8` or `chunks_hex`. The hex form represents arbitrary
byte fragments and permits splitting a UTF-8 codepoint. For example:

```json
{
  "/api/generate": {
    "chunks_hex": ["e9", "9baa0a", "7b22646f6e65223a747275657d0a"],
    "content_type": "application/x-ndjson",
    "first_delay_s": 20,
    "gaps_s": [0, 200]
  }
}
```

This example emits `雪\n` followed by `{"done":true}\n` to probe raw transport
fragmentation; the first line intentionally is **not valid Ollama JSON**. For
a valid stream, use the bytes from `protocol/v1/fixtures/*.upstream.ndjson`.
Delays apply to body output after headers. `gaps_s[N]` is the delay between
chunks N and N+1; omitted gaps are zero. `disconnect_after` closes abruptly
before the chosen zero-based chunk index (or after all chunks if equal to
their count). Omitting a terminal record without `disconnect_after` yields
graceful incomplete EOF. `status` defaults to 200. Large `json` or `body_utf8`
values exercise response sizes independently of model hardware.

The local ASGI tests inject a clock for 20/200-second scenarios and compare
exact response events/bytes. HTTPX's in-process ASGI transport buffers its
responses; these tests do **not** measure network first-byte latency, actual
wall-clock silence, socket disconnects, Edge CPU or Vercel limits. Run the
service over real HTTP in the subsequent relay harness for those measurements.

## Outstanding step 0 experiments

Local SQL/Auth/Edge/external-worker lifecycle, two-identity isolation, optional
FastAPI and maintenance harnesses now exist; see their reports above. Still
required: hosted Edge/Vercel limits, full load/adversarial matrices, release
templates and installer API/CLI/Marketplace capability probes. Spike SQL stays
outside production migration history; local success does not qualify a release.

Hosted experiments need explicitly selected isolated projects, plans/regions,
authorized credentials and a budget. Record actual payload/output bytes,
timings, terminal markers, failures, runtime versions, configured limits,
CPU/memory where available, resource IDs and scoped cleanup. Never include
credentials, prompts from real users or model files in evidence.
