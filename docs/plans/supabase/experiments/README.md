# Supabase implementation experiments

These pages retain detailed procedures and measurements from different source
revisions. They are developer material, not alternative end-user setup guides.
Start with the [documentation index](../../../README.md) for current instructions.
The [progress ledger](../progress.md) owns validation status and
[next steps](../next-steps.md) owns remaining work.

## Runbooks

| Document | Scope |
| --- | --- |
| [Local Edge lifecycle](local-edge-lifecycle.md) | Development spike harness, real transport checks and strict disconnect regression |
| [Operator lifecycle](operator-lifecycle.md) | Versioned install, upgrade, rotation, revocation and removal |
| [Candidate distribution](distribution.md) | Local artifact verification and explicitly gated publication infrastructure |
| [Local installer](local-installer.md) | Separate OAuth/control-service development; hosted browser acceptance is pending |

For automated fresh-stack acceptance, use the [disposable local runbook](../../../setup-supabase-local.md).
A runbook may include dated results; rerunning its commands does not inherit those results.

## Dated evidence

| Report | Original scope/date |
| --- | --- |
| [Step 0 characterization](step-0-local-characterization.md) | Legacy route and worker fixtures, October 4 |
| [Local maintenance](local-maintenance.md) | Deletion recovery, orphan isolation and Cron, October 6 |
| [Worker recovery](worker-recovery.md) | Independent control, Auth refresh and fencing, October 10 |
| [Disconnect signals](disconnect-signals.md) | Edge abort timing investigation, October 11 |
| [Hosted qualification](hosted-qualification.md) | Bounded hosted measurements, October |
| [Independent acceptance](independent-acceptance.md) | Hosted size boundaries, installed wheel and provider removal, October 14 |
| [CLI onboarding](cli-onboarding.md) | First implementation and later October 31 acceptance |

Historical hosted qualification targets were retired. Their credentials and
receipts are not current test inputs. Statements about missing features in an
old report describe that revision, not the latest implementation.

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
wall-clock silence, socket disconnects, Edge CPU limits. Run the
service over real HTTP in the subsequent relay harness for those measurements.

## Running new experiments

Use the [remaining plan](../next-steps.md) to select the next gate. Keep offline,
local integration and hosted evidence separate. Record the source revision,
runtime versions, configured limits, actual payload/output bytes, timings,
terminal markers, failures, resource measurements and scoped cleanup.

Hosted experiments need an explicitly selected isolated target and credentials.
Keep private reports under ignored state directories; never put credentials,
real-user prompts or model files in checked-in evidence. A historical passing
experiment does not qualify a newer release.
