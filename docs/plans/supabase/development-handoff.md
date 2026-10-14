# Development handoff

Updated October 14, 2026 on branch
`supabase`. **Implemented and deployed as a candidate; not qualified for
production.** Both hosted reader-disconnect checks fail. Tests use the explicitly
authorized isolated Supabase Free and Vercel Hobby targets; this handoff does not
authorize paid upgrades or deployment to another unspecified account.

The user-approved physical gateway removals are complete. Both hosted test
clusters are disabled; their application keys/workers are revoked. Projects,
retained data and shared configuration remain. Do not run inference/probes with
the retired sessions. See [October 14 acceptance](experiments/independent-acceptance.md).

## Start here

Read the [next steps](next-steps.md), [progress ledger](progress.md) and
[hosted evidence](experiments/hosted-qualification.md) first. The
[implementation plan](implementation.md) owns architecture and state-machine
invariants; the [compatibility matrix](compatibility.md) owns route behavior and
verification gates; the [installation plan](installation.md) owns hosting
profiles and setup. Record changed decisions there so another agent can continue
without chat history.

Keep Redis as the existing CLI default. Supabase uses Edge by default, includes async jobs, and supports optional FastAPI gateways. All nine existing routes are in scope. Do not substitute a FastAPI-only implementation for a failed Edge spike, broaden Ollama fields incidentally, or implement an automatic cross-backend retry.

Both hosted gateways returned genuine Ollama inference and passed route fixtures,
exact NDJSON and signed private downloads (1.2 MB Edge, 4.2 MB Vercel). They
enforce a measured 135-second synchronous deadline and complete async jobs beyond
400 seconds. The October 14 combined selection has **429 passes**, one strict
local Edge disconnect failure and two opt-in real-model cases deselected; later
probe guards have separate evidence in the ledger. Deno has **48 passes**. Hosted
real-model checks passed separately. Both final hosted disconnect probes also fail:
SQL becomes `succeeded` after the reader closes. Keep both profiles unqualified.

The [operator lifecycle](experiments/operator-lifecycle.md) covers versioned
migrations, enrollment, upgrade/recovery and the verified wheel-backed Vercel
template. Fresh and legacy migration histories have distinct supported paths;
applied files remain immutable. The candidate fixes modern Storage compatibility,
commit locking, drift detection and preservation of API exposure. The local spike
remains separate. Local FastAPI socket cancellation and
[maintenance checks](experiments/local-maintenance.md) pass, but do not close
the remaining hosted envelope, disconnect or installer gates.

## Checkout prerequisites and immediate work

Use uv by default; Conda is optional. Python 3.12 is selected for development,
matching the minimum in [project metadata](../../../pyproject.toml). From a fresh
checkout, prepare the locked environment with:

```sh
uv sync --locked --extra supabase --extra installer
uv run --locked pytest -q tests/test_systemone.py
```

Record actual Python and dependency versions. Include `--extra installer` when
running the complete contract suite or the installer service. Hosted
1,000-job loads, restart/fencing/EOF/slow-reader and corrected actual Auth expiry
checks now have evidence in the progress ledger; wider envelope and installer
onboarding remain unrun.

The experimental toolchain is pinned
in `supabase/toolchain.json`; release qualification remains pending. Local
Supabase integration tests need the CLI and a compatible container runtime;
Python characterization fixtures do not. Real Ollama/GPU access is unnecessary
for deterministic protocol tests.

The user chose preserving the current contract and experimental status. Continue
the remaining hosted envelope and installer acceptance. The
[local installer/adapters](experiments/local-installer.md) are implemented and
fault-tested; no OAuth app exists yet, and the user chose local validation first.
Actual Edge/Vercel adapter deployment/repeat reconciliation, reviewed live removal
and the complete installed-wheel local CLI lifecycle pass. Remaining hosted
performance/resource measurements and fresh browser/project onboarding need
new explicitly selected disposable installations. Hosted OAuth/button work needs
external setup; the user reconfirmed local-first work on October 14.
The [ordered checklist](next-steps.md) maps these to delivery steps 6–7 and keeps
cancellation as an open release gate. Use the strict existing regression and real
provider probes; do not repeat completed work as if implementation has not begun.
No alternative ingress/client cancellation protocol is the current direction.

Shared [protocol schemas/fixtures](../../../protocol/README.md) and the fake
upstream already exist. The [initial characterization report](experiments/step-0-local-characterization.md)
retains the original 162-test evidence as history. Each new slice should remain
independently reviewable and leave the Redis baseline passing.

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

## Hosted prerequisites and remaining dependencies

| Needed for | External inputs | Work possible without them |
| --- | --- | --- |
| Edge qualification | Free project preserved; reviewed test function removed and cluster disabled; new disposable installation needed for more hosted probes | Existing bounded size/adapter/removal evidence is recorded; strict disconnect and broader resource gates stay open |
| Vercel qualification | Hobby project/configuration preserved; reviewed deployment removed and cluster disabled | Existing bounded size/adapter/removal evidence is recorded; strict disconnect and broader resource gates stay open |
| Web installer | Registered Supabase OAuth app, controlled HTTPS callback domain, installer hosting/credential store, maintenance owner; verified Marketplace access | Local service, bootstrap manifest, CLI and provider fault tests are delivered; continue independent CLI/adapter acceptance |
| Real model smoke | Local Ollama 0.40.2 and installed `llama3.2:3b` already verified through both hosted gateways | Repeat bounded real inference after material runtime changes; deterministic tests remain the equality oracle |

Use the existing private operator journals for the selected isolated targets;
do not print secret files or borrow another project for destructive fault tests.
When an external dependency is missing, report affected gates as **not run** and
continue independent work. CLI access does not establish OAuth app/callback
prerequisites. Do not mark the whole backend ready because the available subset
passed.

## Completion evidence and repository precautions

Every implementation slice should state which C/V cases it covers, exact commands/results, unrun hosted cases, migration/rollback implications and any changed limits. Required CI should fail on missing prerequisites; optional local hosted suites may skip only with an explicit reason. A capability that has not passed its declared profile remains experimental.

Keep credentials out of Git and release bundles. Extend ignore/package rules for worker session files and generated CLI state before creating them. Check both `pyproject.toml` and `setup.py` when adding the Supabase extra. Do not include model files in deployment artifacts.

The existing [release workflow](../../../.github/workflows/bump-and-release.yaml)
runs on eligible pushes to `main` and does not currently ignore `docs/**`. A
documentation merge may therefore trigger the release job; review it before
merging. Candidate deployments are isolated test resources, not a public release.

Continue with [the next-steps checklist](next-steps.md), preserving the decision
to leave disconnect qualification open while completing independent work.
Keep failed and unrun gates visible until actual evidence or an explicit reviewed
contract decision closes them.
