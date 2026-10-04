# Development handoff

Reviewed October 4, 2026 against runtime baseline `1ed65b0` on branch `supabase`. **Ready to start development; not yet validated for production.** The remaining uncertainty is explicitly assigned to implementation and hosted experiments below. This handoff does not authorize cloud spending or deployment to an unspecified account.

## Start here

Read the [decisions](index.md), [implementation plan](implementation.md), [compatibility matrix](compatibility.md), then [installation plan](installation.md). The implementation plan owns architecture and state-machine invariants; the matrix owns existing route behavior and verification gates; the installation plan owns hosting profiles and setup. Record any changed decision in those documents so the next agent does not depend on chat history.

Keep Redis as the existing CLI default. Supabase uses Edge by default, includes async jobs, and supports optional FastAPI gateways. All nine existing routes are in scope. Do not substitute a FastAPI-only implementation for a failed Edge spike, broaden Ollama fields incidentally, or implement an automatic cross-backend retry.

No Supabase runtime code, SQL migration, deploy template or new CLI command described in these plans exists yet. Proposed paths and commands are implementation targets. The focused baseline was rerun during this review: `tests/test_systemone.py`, **13 passed**. Current live e2e tests do not establish Supabase behavior.

## Local prerequisites and first work package

Use Python 3.12 or newer, matching [project metadata](../../../pyproject.toml). From a fresh checkout, the existing Python baseline can be prepared with:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/test_systemone.py
```

Record the actual Python and dependency versions. This repository does not yet pin the future Deno/Supabase CLI/Node test toolchain: choose tested versions in the first infrastructure slice and commit their configuration/locks. Local Supabase integration tests need the CLI and a compatible container runtime; Python characterization fixtures do not. Real Ollama/GPU access is unnecessary for deterministic protocol tests.

The first work package is **characterization fixtures plus a reproducible relay spike**, corresponding to delivery step 0:

1. Capture current normalized payloads, default values, errors and response bodies for C01–C09. Add a controllable fake Ollama upstream supporting delayed first output, long silence, exact NDJSON, disconnects and large responses. Do not use sampled model prose as an equality oracle.
2. Write the versioned protocol schemas/fixtures described below. Keep these independent of the Python and TypeScript clients.
3. Prototype the smallest authorized submit/claim/append/read/finish path, with one external fake worker and an Edge relay. Include two identities to check isolation. Temporary spike migrations and resources must remain isolated from production migration history.
4. Exercise a minimal Vercel ASGI relay separately. Probe the required installer APIs/CLI and Marketplace permissions before claiming either button can complete installation.
5. Commit reproducible harnesses and a redacted evidence report under a new `docs/plans/supabase/experiments/` directory. Record runtime versions, project plan/region, configured limits, input/output bytes, timings, terminal-marker behavior, CPU/memory evidence where available, failures and cleanup.

After local characterization passes, shared contract/executor extraction can proceed while hosted access is arranged. Hosted results gate the advertised workload envelope and release, not all local development. Follow the delivery sequence for database, complete Edge routes, streaming/async, FastAPI and installation. Each slice should be independently reviewable and leave the Redis baseline passing.

## Finalize the protocol before production migrations

The plan specifies semantic contracts, not complete wire schemas. Producing those schemas and tests is part of the first implementation slice, not a reason to guess different formats in each component. Resolve and record the following in `protocol/` before database/gateway/worker implementations diverge:

| Contract | Required decisions and fixtures |
| --- | --- |
| RPC envelopes | Exact parameter/result types, protocol version, IDs, UTC timestamp encoding, duration units, UTF-8 byte accounting and stable error codes; distinguish null, missing and empty values |
| Retry identity | Operation ID and canonical request hashing rules; idempotency lifetime; replay of successful submit/append/finish after acknowledgement loss. Worker registration must not rotate its own session again on an identical retry; claims must expose already-assigned work after an ambiguous response |
| Auth boundary | RPC role/grant matrix; how opaque application keys are validated without exposing hashes; worker enrollment-to-Auth-session exchange, revocation, refresh and owner bootstrap. Prove the selected Auth flow works without public signup/SMTP before shipping enrollment |
| Limits and admission | Configuration names/defaults and precedence; finite queued/running/per-caller budgets; queue deadline versus total deadline; async execution deadline; reader-stall timeout; signed-download lifetime; rate-limit retry behavior. Enforce maxima in SQL, not only the gateway |
| Output and errors | Terminal NDJSON detection, sequence/page/checksum encoding, oversized output, incomplete upstream EOF and error mapping before/after HTTP headers; distinguish failed execution from expired retained content |
| Async API | Exact request/status/result schemas and example JSON for every state, following the policy below; authorization and idempotency fixtures shared by both gateways |

Choose conservative finite defaults for currently unspecified tuning values and label them experimental until measured. This does not require reopening the agreed gateway/backend choice. Configuration changes that alter existing Redis behavior require separate characterization and a documented compatibility decision.

## Async v1 policy

Async jobs cover the non-streamed inference operations already supported by the worker: `/api/generate`, `/api/chat`, `/api/embeddings` and `/v1/systemone`. Metadata routes do not create jobs. For chat/generate, omitted `stream` means false and explicit `stream=true` is rejected with 422 before admission; asynchronous stream replay is outside the initial contract. System One retains its existing always-non-streamed normalization, including ignored extra `stream` values.

`POST /jobs` returns 202 with job ID and status location. Job ownership is tied to the authenticated caller scope, not knowledge of the ID. `GET /jobs/{id}` returns 200 with the durable state even when inference failed. Unknown or other-caller IDs return 404 after authentication, without disclosing ownership. Result retrieval returns 202 while queued/running, 200 for a retained successful result, 409 with a stable error code for failed/cancelled/execution-expired work, and 410 when retained result content has expired. Content-expiry tombstones must last for the advertised retrieval/idempotency window; after their documented expiry, 404 is permissible.

The default successful result is the operation's original JSON, with the requesting gateway's response-size limit enforced before writing headers. If a completed result exceeds that limit, return a documented `result_too_large` error (413) and leave the job successful and downloadable; do not rerun inference. Explicit download mode returns a small JSON descriptor with a short-lived signed URL, size, checksum, content type and expiry. Generate URLs only after authorization, never persist them as the durable result reference, and explain that they are bearer credentials.

Submission disconnect does not cancel an accepted async job. Idempotency keys recover ambiguous submission outcomes. Cancellation is idempotent and races atomically with finish: a winning terminal state cannot be overwritten. Async jobs retain the same capacity, attempt fencing, retry and execution-deadline rules as synchronous jobs, without a consumer lease.

## Hosted prerequisites and remaining dependencies

| Needed for | External inputs | Work possible without them |
| --- | --- | --- |
| Edge limits | Isolated Supabase test project(s), Free/paid coverage, selected region, authorized deployment credentials and test budget | Fixtures, local SQL/RLS tests, fake worker and relay implementation |
| Vercel profile | Isolated Vercel project/team, plan-specific test access, deployment configuration and budget | ASGI factory/template and deterministic request-reader tests |
| Web installer | Registered Supabase OAuth app, controlled HTTPS callback domain, installer hosting/credential store, maintenance owner; verified Marketplace access | Bootstrap manifest, CLI path, provider adapters with mocked failure cases |
| Real model smoke | Worker machine, local Ollama version, selected models/digests, model-specific capabilities and hardware | Deterministic transport, lease, retry and API tests |

Do not assume credentials are present, print secret files, or borrow a production project for destructive fault tests. When an external dependency is missing, report the affected gates as **not run** and continue independent local work. Do not mark the whole backend ready because the available subset passed. No hosted resources were provisioned during planning.

## Completion evidence and repository precautions

Every implementation slice should state which C/V cases it covers, exact commands/results, unrun hosted cases, migration/rollback implications and any changed limits. Required CI should fail on missing prerequisites; optional local hosted suites may skip only with an explicit reason. A capability that has not passed its declared profile remains experimental.

Keep credentials out of Git and release bundles. Extend ignore/package rules for worker session files and generated CLI state before creating them. Check both `pyproject.toml` and `setup.py` when adding the Supabase extra. Do not include model files in deployment artifacts.

The existing [release workflow](../../../.github/workflows/bump-and-release.yaml) runs on eligible pushes to `main` and does not currently ignore `docs/**`. A future documentation merge may therefore trigger the release job; review that workflow deliberately before merging. This planning commit stays on `supabase` and does not deploy or publish anything.

Start development with the first work package above. Product architecture is settled; protocol details, workload measurements and installer integration proof are assigned deliverables with clear gates, rather than hidden assumptions.
