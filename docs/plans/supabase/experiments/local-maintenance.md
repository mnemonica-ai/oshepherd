# Local maintenance verification

> Dated evidence. Results and feature gaps below describe the recorded revision.
> See the [progress ledger](../progress.md) for current status and the
> [documentation index](../../../README.md) for current user instructions.

October 6, 2026. Experimental SQL remains outside production migration history.
This extends the [local lifecycle](local-edge-lifecycle.md); hosted cleanup/load
and production installation remain unverified.

`queue.sql` now stores result deletion intent before removing references and
bounds chunk/receipt removal. `maintenance.sql` adds cluster-scoped orphan
scanning, deletion claims and acknowledgement receipts. SQL confirms Storage
metadata absence before forgetting intent. The Python operator runner deletes
bytes through Storage's HTTP API and carries no worker identity.

The scan uses Storage's existing `(bucket_id,name COLLATE "C")` index. The local
database's `en_US.UTF-8` collation broke the original punctuation range; explicit
bytewise comparisons fixed it. Production preflight must verify this index on
the selected Storage version. Only the two service-role maintenance RPCs use
the migration identity's Storage metadata privileges; the ordinary runtime
owner and workers receive no administrative Storage access.

The demo supervisor runs worker, Edge and maintenance as separate processes.
Ctrl-C stops those process groups with bounded shutdown, preserving the database
and user's Ollama. Maintenance logs live privately in the demo state directory
and contain aggregate counters rather than paths or payloads.

## Reproduce

Apply all six spike files atomically to the fixed local profile, then run:

```sh
uv run --locked python -m scripts.supabase_local apply-spike
uv run --locked pytest -q tests/contracts/test_supabase_maintenance.py --tb=short
uv run --locked pytest -q tests/integration/supabase -k maintenance --tb=short
```

Results: seven service-free tests passed; four real local Auth/SQL/Storage/Cron
tests passed in 33.88 seconds. The latter verified:

- Deletion intent survives a lost purge acknowledgement, expired claim and
  committed-but-lost completion response. Completion replay uses the same UUID
  and exact request. False HTTP success cannot discard existing objects.
- Bounded orphan scanning removes a cancelled attempt's old object while
  preserving live, committed and other-cluster objects. Worker maintenance calls
  and cross-cluster acknowledgement paths are denied.
- A 131-chunk stream purges in finite passes, keeps its successful content-expiry
  tombstone, and removes attached receipts before deleting the tombstone.
- A temporary one-second Cron job expires and purges a generated cluster's job
  without gateway/worker processes. Its schedule is removed after the test;
  private sweep execution is denied to all Data API roles.

The full service-free suite passed 276 tests in 15.07 seconds. With maintenance
running, persistent doctor, JSON inference and nine real-model NDJSON records
ending in genuine `done: true` passed against existing `llama3.2:3b`, eight tokens.
The supervisor was stopped afterward. Model prose is not an equality oracle.
The actual `supabase-maintain --once` command also passed using a temporary
private operator env file, which was removed afterward; output contained only
aggregate counters. Rebuilt sdist and wheel inspection found the maintenance
runtime and excluded private state, tests and checkout scripts.
The combined local regression suite passed 25 tests in 139.56 seconds, explicitly
excluding the recorded proxy-disconnect failure and separate opt-in model test.

## Database schedule and operator runner

The local stack exposes `pg_cron` and `pg_net` as available extensions; both were
initially uninstalled. Earlier diagnostics did not display all result sets and
incorrectly suggested absence. Actual Cron execution is now verified. Supabase
Cron stores schedules/run history in the database and supports SQL function
jobs. [Supabase Cron](https://supabase.com/docs/guides/cron)

Enable or remove only this experiment's two named jobs:

```sh
uv run --locked python -m scripts.supabase_local schedule-cleanup
uv run --locked python -m scripts.supabase_local unschedule-cleanup
```

Reaping runs every ten seconds; SQL purge runs every minute. Each sweep visits
at most four clusters and eight jobs per cluster, rotates its private cursor,
skips locked clusters, and has a five-second statement/100-ms lock budget.
Repeating scheduling reused the same two job IDs and produced successful runs.
An unexpected command under an owned job name causes a collision error; removal
also checks ownership. Unrelated schedules are preserved. Purge also prunes at
most 256 completed run-history rows older than two days for these job names.
The interval syntax is supported by [pg_cron](https://github.com/citusdata/pg_cron).

The additional real collision regression passed in 1.09 seconds: repeated
scheduling returned the same job ID, and both install and removal refused a
changed command while preserving it. The test used only a generated job name
and removed that schedule afterward. Cron's supported API performed the test
mutation; direct table UPDATE is not granted to the local migration identity.

Cron alone does not remove Storage bytes. Keep `supabase-maintain` running under
an operator supervisor, or use the local demo's `serve`. The operator command
accepts a private env file with project URL, server-only key, schema and selected
cluster; `--once` performs one bounded cycle. Workers never need this key.

Orphans must be at least ten minutes old, then wait another five minutes before
deletion. Current result references and active attempts are protected. Claimed
deletions lease for 45 seconds and retry with bounded backoff. Storage deletion
stays durable if the runner, gateway or provider request disappears.

These are local functional checks, not a throughput/cost benchmark. Hosted
extension permissions, Storage versions, installer schedule reconciliation and
full load/retention qualification remain open. The separate Edge proxy HTTP
disconnect gate still fails and is recorded in the lifecycle report.
