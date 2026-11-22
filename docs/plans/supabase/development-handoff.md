# Supabase development handoff

Updated November 22, 2026. Work on branch `supabase`. Use the
[documentation index](../../README.md) for user instructions, the
[progress ledger](progress.md) for dated results, and [next steps](next-steps.md)
for remaining work. Supabase is primary; Redis is supported. The candidate is
experimental and unpublished.

## Start here

```sh
uv sync --locked --extra supabase --extra installer
uv run --locked pytest -q tests/contracts tests/test_systemone.py
```

For live Docker/Ollama validation, follow the [disposable acceptance runbook](../../setup-supabase-local.md).
It requires a fresh work directory, Docker, pinned Supabase CLI, `psql` and
installed models. The [manual local walkthrough](../../supabase-walkthrough.md)
is the path for a persistent database with Studio. Neither workflow requires
publication. Do not reset unrelated containers or edit applied migrations.

## Source map

| Area | Location |
| --- | --- |
| CLI onboarding and operator lifecycle | `oshepherd/backends/supabase/` (`share_cli.py`, `join_cli.py`, `client_cli.py`, `operator.py`) |
| Worker execution and local logs | `worker.py`, `startup.py`, `worker_logging.py` in that package |
| Packaged SQL and release metadata | `oshepherd/backends/supabase/release/` |
| Edge source and development SQL | `supabase/functions/ollama/`, `supabase/migrations/` |
| Shared inference execution | `oshepherd/worker/executor.py` |
| Wire contracts and deterministic fixtures | `protocol/` |
| Offline checks and real-service integration | `tests/contracts/`, `tests/integration/supabase/` |

## What works

The candidate includes hosted create/select setup through the Management API,
local Docker bootstrap, worker invite/join and saved-session restart, named client
keys, private rotating worker logs, cleanup and optional user services. Redis
retains its existing commands. Implementation is distinct from provider and
resource qualification; consult the ledger before making a validation claim.

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

The [latest offline verification](progress.md#november-22-offline-verification)
records the source revision and test count. Earlier real-model, installed-wheel
and hosted runs are dated separately in the ledger. Do not reuse a historical
pass as acceptance for a new source revision, or interpret a manual smoke test
as completion of the full matrix.

The [remaining plan](next-steps.md) covers Edge CPU limits and cancellation,
physical second-machine validation, hosted lifecycle coverage and resource/cost
measurements. Public distribution and hosted browser authentication are later
work. Test locally built wheels using [candidate installation](../../install.md#install-the-0025a1-candidate).

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

The canonical policy is in the [Supabase protocol](../../../protocol/supabase/v1/README.md#output-state-and-async-api).
It defines admission, request/response schemas, ownership, result/download
statuses, retained content and cancellation. Keep V22 in the
[compatibility matrix](compatibility.md) aligned with that contract.
