# Oshepherd protocol fixtures

`v1/` starts delivery step 0 with a **characterization contract**, captured from
runtime baseline `1ed65b0` using the dependency versions in `requirements.txt`.
The checked-in JSON and NDJSON are independent of the Python and TypeScript
clients. Tests must consume these files, rather than manufacture expected values
using the code under test. No sampled model output is used.

November 15, 2026 compatibility amendment: `embeddings-prompt-only` now records
successful legacy SDK requests. Both Redis and Supabase accept `prompt` as an
alias for `input`, with existing `input` taking precedence. The normalized queue
schema and worker payload remain unchanged. The former HTTP 500 is a fixed
compatibility defect, not an expected failure.

This slice does not freeze the Supabase RPC, enrollment or async wire protocol.
Those schemas and identity/limits/authorization decisions remain prerequisites
for production migrations. `v1` identifies the fixture format, not a claim of a
deployed Supabase protocol version.

Run the characterization checks with:

```sh
.venv/bin/python -m pytest -q tests/contracts tests/test_systemone.py
```

## Files and semantics

| Artifact | Contract |
| --- | --- |
| `v1/fixtures/routes.json` | C01–C09 HTTP requests, normalized Celery envelopes, worker results, HTTP bodies, errors, content types and metadata inputs |
| `v1/schemas/*.request.schema.json` | Draft 2020-12 structural schemas exported from the five existing request payload models |
| `v1/fixtures/upstream.json` | Exact deterministic raw Ollama responses, before SDK processing |
| `v1/fixtures/worker.json` | SDK-processed worker response snapshots, including added null fields |
| `v1/fixtures/*.upstream.ndjson` | Raw upstream stream bytes, including Unicode and a genuine final `done` record |
| `v1/fixtures/*.legacy.ndjson` | Exact serialized worker records forwarded by the current Redis gateway |

Schemas validate **normalized payloads**. JSON Schema does not insert defaults,
coerce values or discard unknown fields. `x-extra-behavior` records the current
normalization rule: generate/chat/embeddings/show drop unknown fields; System One
preserves them, including question fields and an ignored `stream` parameter.
Chat message extras are also discarded. Future cross-language normalizers must
compare their output with the stored `normalized` objects. The schemas alone
do not establish full Pydantic coercion parity.

Missing, null and empty values have separate cases. All defaults are written
explicitly in the normalized envelopes. JSON object key order is irrelevant to
JSON comparisons. NDJSON equality is byte-for-byte, including JSON whitespace,
escaped Unicode, null SDK fields and the trailing newline; transport chunk
boundaries may differ. Upstream duration fields remain Ollama's nanoseconds.
The fixed upstream timestamp is a UTC string; registry heartbeat fixtures use
the baseline's ISO 8601 offset encoding. These observations do not choose future
RPC duration/timestamp encodings or request hashing rules.

`workers` are Redis hash inputs, not a new metadata API. The clock is fixed in
tests; real registry filtering, name deduplication, running-model concatenation
and show wrapping are exercised. Conflicting live digests currently use the
last scanned tag; Supabase must follow the plan's explicit alias/digest policy.

## Known differences to resolve during extraction

Malformed/invalid generate/chat/embeddings/show input currently returns an
unhandled plain-text HTTP 500; System One returns a JSON 422 before enqueueing.
The new shared validator should deliberately standardize invalid requests to
422, with a recorded compatibility decision, instead of reproducing these 500s.
Legacy worker-result errors use the existing route JSON 500 mapping. Publish
and result exceptions are caught by System One but escape the other inference
routes. Legacy streaming errors include `done: true`; this is an error record,
not a successful upstream terminal marker.

The SDK adds null fields to generate/chat response objects. Consequently raw
Ollama JSON/NDJSON is not identical to the bytes delivered by Oshepherd. The
worker tests run the actual SDK against the fake upstream to verify this
boundary, including embeddings' `input` to legacy `prompt` translation.

The existing SDK and worker can silently finish a stream without `done: true`.
The Supabase executor must report incomplete output as a failure. Fake upstream
tests distinguish graceful incomplete EOF from an abrupt connection exception;
live socket/platform kill behavior still needs the relay experiments.

Future schemas should be added in a separate reviewed slice with RPC role,
idempotency, limits, output and async state fixtures, following the
[handoff](../docs/plans/supabase/development-handoff.md#finalize-the-protocol-before-production-migrations).
