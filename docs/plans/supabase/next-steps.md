# Supabase next steps

Updated November 22, 2026. Supabase is the primary broker on this branch; Redis
remains a legacy option. PyPI publication is outside the current milestone.

## Completed local milestone

A fresh Docker Supabase database is installed through the CLI, with no spike SQL
preloaded. The Edge gateway routes official Ollama Python requests to real local
Ollama. `invite` and `join` onboard a second server; the first worker is stopped
to prove routing to the joined worker, and inference is repeated after restart.
Generate/chat JSON and streaming, legacy embeddings, metadata and real System
One (`tev1:4b`) passed. The two Ollama servers ran on the same machine.

See the [walkthrough](../../supabase-walkthrough.md) and
[local acceptance details](../../setup-supabase-local.md). The foreground local
path does not depend on package publication. Later branch-cleanup reruns exposed
a repeatable Edge CPU limit at the joined-worker stage; the earlier successful
runs remain historical evidence, not a passing result for this revision.

## 0. Diagnose the local Edge CPU limit

The latest two fresh-stack reruns passed first-worker inference, then hit Edge
CPU soft/hard limits on the joined worker: System One returned 503, and a
standard generate rerun returned 546 `WORKER_LIMIT`. Both cleaned up successfully.
No gateway/SQL/inference-worker source changed during the branch split. Preserve
the private failure reports in the [ledger](progress.md#branch-cleanup-follow-up).

Measure isolate reuse, CPU consumption and polling under this sequence before
choosing a fix. Keep the declared provider envelope; increasing client timeouts
or hiding failures with retries does not establish acceptance. Re-run primary,
joined and restarted stages after a justified fix, with real System One included.

## 1. Verify a physical second machine

Use a reachable installation and a fresh private invitation. Install a locally
built candidate wheel on a second machine, run Ollama there, join it and prove
that it handles real inference when the first worker is unavailable. Restart it
without the invitation and verify its saved restricted session. Confirm that
no operator credentials were transferred.

The current loopback Docker configuration cannot be reached from another host.
A hosted HTTPS project is the implemented route for that test. General LAN or
self-hosted-origin configuration is a separate design/acceptance task; don't
rewrite invitation bindings as a workaround.

## 2. Complete fresh hosted setup acceptance

The wizard implements organization/project creation and selection, Free-plan
checks, durable reconciliation after uncertain creation, Management SQL setup,
Edge deployment and owner/worker enrollment. It needs a full run on a newly
selected disposable hosted project. Local container tests do not prove the live
Management API's response/error shapes or the new hosted `postgres` privileges.

Authentication decision (November 22): keep the current direct-API setup and
manual Supabase personal access token for this milestone. The user creates the
token in Supabase and enters it at `oshepherd share`'s hidden prompt; setup saves
it privately for recovery. Hosted setup makes HTTPS requests to Supabase rather
than invoking Supabase CLI commands, and it does not reuse `supabase login`.
Browser-based authentication is follow-up work, not a blocker for this hosted test.

Verify fresh and existing-project paths, interrupted/resumed setup, repeat
installation, real SDK inference, second-machine joining, key rotation,
revocation and overlapping-version upgrades. Measure cloud readiness separately
from worker readiness and inference. Keep the explicit ownership and collision
checks. The earlier hosted qualification cluster was disabled and its reviewed
function removed; old receipts, keys and sessions are not reusable test inputs.

## 3. Close the stream-disconnect cancellation gate

Both local Edge and earlier hosted probes can finish SQL work as `succeeded`
after the HTTP reader closes. The [signal investigation](experiments/disconnect-signals.md)
records a late abort after stream completion. Keep the strict assertion:

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py \
  -k first_record --tb=short
```

Acceptance requires an actual reader closing after a nonterminal record, SQL
becoming `cancelled`, the worker stopping its upstream read and resources being
released within the tested bound. Normal completion must stay successful, and
accepted async jobs must survive submission disconnect. Persistent FastAPI's
socket cancellation does not qualify the Edge proxy. A change of client or
transport contract is not part of the current implementation direction.

## 4. Finish operational qualification

The bounded hosted load, Auth refresh, worker recovery, size boundaries and
paused-reader checks already have historical evidence. Remaining measurements
include sustained server backpressure, gateway memory, Redis-relative latency,
provider invocation/resource ceilings and account quota/cost headroom. Keep
four-active-job caller limits and enrolled worker capacity while testing.

Repeat relevant Python/Deno and real-model checks after material changes. Verify
clean wheel/source archives against the manifest and test installed commands
outside the checkout. Publication remains deferred; local wheel testing is
sufficient for this milestone.

## Later: optional browser onboarding and distribution

Add browser-based authentication to the CLI sharing journey so interactive users
can authorize their Supabase account without manually creating and copying a
token. Evaluate a supported Supabase login integration or an OAuth flow before
choosing the implementation; do not assume CLI credentials are automatically
available to oshepherd. Retain manual tokens for headless use. Acceptance should
cover successful consent, denial/cancellation, interrupted login, credential
expiry/revocation and private credential storage while preserving explicit
project selection and resumable setup.

The local OAuth installer exists, but hosted consent/refresh/revocation, a
registered Supabase OAuth app, controlled callback domain, durable installer
host and browser recovery still need acceptance. These are later work, not
prerequisites for the local CLI workflow. No public install button is claimed.

A public package release needs its own explicit scope, artifact review and
publishing prerequisites after the chosen qualification gates pass. Keep the
candidate experimental and `qualified: false` while cancellation is unresolved.
