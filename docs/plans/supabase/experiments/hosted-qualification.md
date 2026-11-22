# Hosted Supabase candidate qualification

> Dated evidence. Results and feature gaps below describe the recorded revision.
> See the [progress ledger](../progress.md) for current status and the
> [documentation index](../../../README.md) for current user instructions.

Recorded October 2026; revised November 15 to focus on Supabase Edge. This is
historical evidence, not a fresh-project wizard acceptance claim. The isolated
Free project ran PostgreSQL 17.11, with Supabase CLI 2.116.0 and a protected
operator connection. No paid upgrade was used. Existing schema exposure and
unrelated project resources were preserved.

The test cluster is now disabled and its reviewed Edge function removed. Retained
data, shared schedules and project configuration remain. Reproduction requires a
new explicitly selected disposable installation; retired keys/sessions and
receipts must not be reused. See [independent acceptance](independent-acceptance.md).

## Compatibility findings and fixes

The first hosted migration rolled back: versioned Storage no longer provides a
unique bucket/path constraint. Its SQL error was initially misclassified as a
resource collision because the exception classifier searched the full SQL
context. The runner now uses terse PostgreSQL diagnostics and matches only an
exact primary error. Regression tests cover misleading exception text in context.

The original applied migrations remain unchanged. A new consolidated baseline
supports fresh installs, while additive migrations upgrade the old history.
Both paths use an Oshepherd-owned commit trigger to verify a current object
exists, reject archived/delete-marker rows, and lock its metadata through commit.
Worker upload/overwrite restrictions remain enforced by RLS. New fingerprint
migrations cover trigger enablement and runtime Storage grants, including
revoked schema usage without a name-resolution failure. No provider table or
unique index was added or altered.

Final review reproduced a second Storage race: a key-share lock allowed a
non-key metadata update while the commit trigger's transaction remained open.
Versioned Storage can archive objects through such updates. Additive migration
`20261011000500` changes the lock to `FOR SHARE`. The real two-session regression
first fails against key-share, then verifies a non-key update times out while
the trigger transaction is held and succeeds after rollback. Already-applied
migration bytes are preserved.

Local testing also exposed PostgreSQL's warning-only ineffective `GRANT` when
the local `postgres` role lacks schema grant options. Configure now selects
`supabase_admin` only on the isolated local target, and install checks grant
options before mutation. The single missed grant in the earlier development
attempt was explicitly reconciled locally before upgrading. Hosted installation
uses its original `postgres` identity.
The local role transition also reconciles only exact owned cleanup commands;
its regression verifies conflicting commands and unrelated jobs survive.
The broader local run also saw a bootstrap failure in the new Storage case;
its isolated rerun passed. A captured response identified `PGRST106`: the legacy
receipt-upgrade test reapplied spike exposure and removed the production API.
The mutable spike exposure script now preserves both installed schemas, and
the actual upgrade regression verifies that preservation. Logs also recorded 152 schema-cache reloads
over the preceding ten minutes. Migration replay had rewritten unchanged API
role settings and notified PostgREST for every file. Exposure reconciliation now
updates and notifies only when the setting changes; a real catalog-version
regression verifies replay leaves the unchanged role setting intact.

## Actual results

| Check | Observed result |
| --- | --- |
| Fresh hosted install, subsequent upgrade, schema doctor | Passed; configured installation, matching fingerprint |
| Hosted Edge deployment and opaque application-key auth | Passed; unauthenticated health returns 401 |
| Actual enrolled CLI worker and local Ollama 0.40.2 | Passed; `llama3.2:3b` returned genuine completion, three generated tokens, 579 response bytes, 4.72 seconds |
| Generate/chat/legacy embeddings/SystemOne fixtures and metadata routes | Passed through real hosted Edge, SQL/Auth and loopback fake upstream |
| Streaming byte compatibility | Generate and chat match the committed NDJSON fixtures exactly |
| Async idempotency and private Storage download | Passed; same job ID on replay; 1,200,347 bytes, signed download size and SHA-256 verified |
| Hosted early-record delivery | First nonterminal record received in 0.84 seconds; checked-in probe repeated at 1.01 seconds, before completion |
| Strict HTTP reader disconnect → SQL cancellation | **Failed**; SQL state became `succeeded`, not `cancelled` |
| Actual hosted cleanup and cluster isolation | Twenty latest Cron runs succeeded; a valid key from the separate test cluster was denied |
| Service-free contracts plus local operator/Storage regression selection | 313 passed in 42.35 seconds; one upstream TestClient deprecation warning |
| Final combined contracts and local integration suite after lock fix | 370 passed, one failed, two opt-in real-model cases deselected in 112.62 seconds; only the known strict local Edge disconnect failed |
| Extended soak, hosted saturation/backpressure, clean-machine OAuth installer | Not run |
| Synchronous application deadline | 504 `execution_expired` after 135.23 seconds |
| Async lifetime | Fixture completion retrieved after 409.14 seconds |
| Final post-upgrade route probe | Routes, exact NDJSON, auth and downloads pass; first nonterminal record at 0.55 seconds; disconnect still fails |

The [signal investigation](disconnect-signals.md) observed the abort only after
stream completion. Keep the strict cancellation assertion and `qualified: false`.
A passing inference request does not qualify the hosted profile.

## Further bounded evidence

The October independent checks exercised 1,000 exact jobs, Auth expiry/refresh
with saved sessions, worker restart/generation fencing, incomplete EOF, retry
exclusion after durable output, explicit cancellation and cleanup after expiry.
Four async jobs succeeded with peak worker concurrency two and queued work;
metadata remained responsive. Expired content retained a success tombstone while
maintenance removed the associated objects after quarantine.

Near-limit and oversized request/output cases and paused NDJSON consumption are
recorded with exact Edge sizes/times in
[independent acceptance](independent-acceptance.md#hosted-size-and-slow-reader-evidence).
These do not establish sustained server backpressure, gateway memory,
Redis-relative latency, provider invocation ceilings or remaining account quota.
The measured 135-second timeout is the application/SQL deadline.

## Reproduce on a new isolated installation

Use the [operator runbook](operator-lifecycle.md), enroll a worker with capacity
two, exchange its one-use challenge and stop other workers for this installation.
The checked-in probe uses a loopback fake upstream, real gateway/SQL/Auth/Storage,
bounded jobs and a 1.2 MB private download. Its strict disconnect assertion is
expected to remain failing until the proxy behavior is resolved.

```sh
uv run --locked python -m scripts.supabase_hosted_check --help
```

Select the explicit connection, state and worker-session files required by that
command. Its optional `--lifetimes`, `--bounded-lifecycle` and `--envelope` modes
exercise separate bounded scenarios. The envelope mode requires an idle
installation and cancels only its own unfinished jobs. Retain failed reports;
client pauses must not be described as measured server backpressure.

Actual Edge adoption, Management upgrade and repeat reconciliation passed after
source-relative module paths were used to reproduce the official bundle hash.
Reviewed removal and acknowledgement-loss recovery are documented separately.
Fresh hosted wizard/physical second-machine acceptance, wider resource/cost
qualification and hosted OAuth remain open. Follow [next steps](../next-steps.md).
