# Supabase development handoff

Updated November 15, 2026. Work on branch `supabase`. Supabase is the primary
broker and Edge is the main gateway; Redis remains a supported legacy backend.
The candidate is experimental, unpublished and not production-qualified.

## Start here

Read the [walkthrough](../../supabase-walkthrough.md),
[progress ledger](progress.md) and [remaining plan](next-steps.md). The current
milestone is local CLI setup and the existing Ollama endpoint set. Publication,
API expansion and hosted OAuth are deferred.

```sh
uv sync --locked --extra supabase
uv run --locked pytest -q tests/contracts tests/test_systemone.py
uv run --locked python -m scripts.supabase_local_acceptance \
  --work-dir "$PWD/.oshepherd/local/new-handoff-run" --decision-model tev1:4b
```

The last command requires Docker, Supabase CLI 2.116.0, `psql`, Ollama and already
installed text/embedding/decision models. Omit the decision flag if that model is
not installed, and do not claim System One acceptance for that run. It creates
and removes its own stack. Never reset unrelated containers or edit applied SQL.

## What works

Fresh local database installation, repeat owner/bootstrap, real Edge SDK calls,
second local Ollama server invitation/join and restart passed. Both servers ran
on the same host; the harness verified routing to the joined worker by stopping
the primary worker. Real `tev1:4b` passed after an authorized model refresh.
Legacy embeddings accepts `prompt`; decision-capable models advertise System One.

The hosted wizard implements project/organization creation/selection, Free-plan
checks, Management SQL, owner setup and Edge deployment. Local service testing
covers launchd and systemd. Fresh live hosted wizard acceptance remains open.

## Contracts to preserve

- Existing Redis CLI defaults and endpoint behavior stay intact.
- Supabase requests require application keys; workers receive restricted Auth
  identities and private one-use invitations, never administrator credentials.
- SQL leases, generations and attempt fencing remain authoritative. Durable
  output prevents retry; missing stream EOF is an error, not a synthetic success.
- The current synchronous maximum is 135 seconds, Edge request/response maximum
  is 1 MiB and async retained output maximum is 32 MiB.
- Migrations are immutable. The manifest selects fresh/legacy history, validates
  hashes and drift, and owns exposure/schedules. Refresh packaged assets after
  changing Python or Edge source with `python -m scripts.build_supabase_release`.
- `uninstall` disables a cluster; it does not delete the provider project/data.
- Stream-disconnect cancellation through Edge remains a strict failing gate.
  Do not weaken it or claim that persistent FastAPI cancellation fixes the proxy.

## Evidence and next work

The focused branch has 545 passing offline Python tests after removing the
separate hosting adapter. Earlier Edge evidence has 50 Deno passes and three
selected local integration passes. Fresh real-model acceptance has 13 checks
per primary/joined/restarted stage in the earlier run. The latest two reruns
failed at the joined worker with Edge CPU-limit responses 503/546, while first-worker
inference and cleanup passed. Diagnose that resource gate before claiming a new
full acceptance pass. Reports are private and ignored; consult the
ledger for their locations and the dates/scope of older hosted measurements.

Next validate a physical second machine and the full fresh hosted wizard on a
new explicitly selected disposable installation. Earlier hosted clusters were
disabled and their reviewed gateway removed. Wider resource/cost qualification
and reliable Edge cancellation remain open. Local wheel testing requires no
PyPI release. Hosted OAuth needs separate app/callback/host prerequisites.

## Protocol contracts to preserve and audit

Candidate wire schemas and implementations are present in `protocol/`, SQL and
both clients. Preserve and audit the contracts below against the remaining
compatibility gates. Any changed format needs versioned fixtures and a reviewed
migration; do not guess a different format in another component:

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
