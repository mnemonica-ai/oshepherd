# Supabase next steps

Updated October 11, 2026, after implementation commit `e29885d`.

The candidate is implemented and deployed to the authorized isolated Supabase
Free and Vercel Hobby targets. Both return genuine local Ollama inference, pass
route/NDJSON/private-download probes, enforce a measured 135-second synchronous
deadline, and complete async work beyond 400 seconds. The final combined suite
has 370 passes, one known strict local Edge disconnect failure, and two opt-in
model cases deselected; real-model hosted checks passed separately.

Both hosted reader-disconnect checks fail: SQL reaches `succeeded` after the
HTTP reader closes. Neither hosting profile is qualified. See the
[hosted evidence](experiments/hosted-qualification.md) and
[full progress ledger](progress.md). Installation includes a CLI and versioned
migrations; provider automation, OAuth and physical resource removal are pending.

## 1. Resolve hosted disconnect cancellation

This is the first release blocker. Reproduce with the strict
[hosted probe](experiments/hosted-qualification.md#reproduce-the-strict-hosted-probe)
for each profile, and with the existing local Edge regression:

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py \
  -k first_record --tb=short
```

The [signal investigation](experiments/disconnect-signals.md) now records late
signals on both hosted providers and the absence of Edge handler completion info.
The next cancellation step is a supported lifecycle/transport decision, rather
than assuming another local listener will repair the measured behavior. Any
further tracing should use protected diagnostics with
job IDs, event names and timings; keep credentials and request/output content out
of reports. Determine whether a supported lifecycle hook can reach SQL cleanup.
The Vercel Python candidate cannot claim that Node.js cancellation opt-in fixes
its failure; the documented limitation is linked in the hosted report.

On October 11 the user selected preserving the current contract and experimental
status while continuing independent qualification and installer work. No
alternative ingress or client cancellation protocol is the next implementation
direction. Keep this release gate open; the next active work package is step 2
below.

If the selected host cannot supply a reliable signal, record a supported
transport/deployment decision in the architecture and compatibility documents
before changing the product contract. Persistent FastAPI cancellation already
has local socket evidence, but does not qualify Edge or Vercel. Keep Edge as the
Supabase default and retain the strict assertions while investigating.

Acceptance: an actual client closes after a nonterminal record; SQL becomes
`cancelled`, the worker stops its upstream read, and the gateway releases its
reader/RPC resources within the tested bound. Also verify a normal completed
response is not cancelled and accepted async work survives submission disconnect.
Do not skip, weaken or replace the failing hosted assertion with mocked evidence.

## 2. Complete hosted limits, load and recovery qualification

Work that is independent of disconnect propagation can proceed alongside step 1.
The lifetime experiment is now a checked-in, bounded `--lifetimes` mode in the
hosted probe and passed against both profiles. Its
[commands and parameters](experiments/hosted-qualification.md#reproduce-hosted-lifetime-checks)
are recorded. Extend the reproducible harness for the remaining measurements.

The additional [bounded lifecycle mode](experiments/hosted-qualification.md#reproduce-bounded-lifecycle-checks)
covers capacity-two queueing, explicit cancellation, silence deadlines and result
expiry/maintenance on dedicated idle installations. Treat it as a functional
subset; sustained load, slow-reader/backpressure, restart fencing and a soak
through real Auth expiry/refresh remain required measurements.

Measure both authorized profiles with delayed/silent upstreams, queue saturation,
slow readers, near-limit JSON/NDJSON, incomplete EOF, restarts and concurrent
cancel/finish. Cover retry exclusions after durable output, capacity and generation
fencing, result expiry, signed-download authorization/expiry, orphan cleanup and
actual scheduled maintenance. Run a bounded soak spanning an actual worker Auth
expiry/refresh cycle, checking heartbeat freshness and independent control work.

Distinguish application deadlines from provider invocation ceilings. The observed
135-second response does not independently measure the configured 150-second
Vercel ceiling. Collect latency, SQL/RPC request counts, output/storage growth,
resource observations where available, and remaining Free/Hobby quota. Set finite
duration/concurrency/polling bounds before running; keep testing within the
existing Free/Hobby authorization, without paid upgrades or model downloads.

Acceptance: a redacted evidence report maps each workload to the
[compatibility gates](compatibility.md), includes failed/unrun cases and cleanup,
and supports a conservative advertised time/size/concurrency envelope. A short
smoke test does not establish sustained capacity or a completed soak.

## 3. Verify clean-machine CLI onboarding

Use a clean checkout/environment and the pinned uv/toolchain workflow in the
[operator runbook](experiments/operator-lifecycle.md). Exercise configure, install,
deploy, enroll, login, doctor and bounded real inference. Stop/restart between
stages and repeat install/upgrade to prove resumability without regenerating
identities, keys or sessions. Verify fresh and legacy migration paths, drift
rejection, rotation/revocation and cluster disablement.

Acceptance: the documented commands work without hidden development state or
Conda; the verified release wheel supplies its runtime/assets; workers receive
only their own Auth session and publishable configuration. Keep the current
distinction between `uninstall` (cluster disablement) and provider deletion.

## 4. Implement provider deployment and removal adapters

Extend the shared operator engine with actual ownership/plan preflight, existing
API-schema discovery, deployment, configuration and independent deployment
verification. Preserve unrelated resources/exposure and recover lost provider
acknowledgements. Deployment remains separate from enrollment. Provider deletion
must identify owned resources and account for shared schemas, schedules and
retained data; avoid presenting cluster disablement as physical removal.

Acceptance: repeatable installation, upgrade and recovery on the isolated targets,
with credentials absent from logs/bundles/worker files and no duplicate resources.
Test removal only against explicitly selected disposable owned resources, with
the concrete destructive operation reviewed before execution.

## 5. Deliver the OAuth installer and deploy-button flow

Build on the verified operator/adapters rather than a second installation engine.
Implement project selection, ownership binding, private resumable journals,
credential handling, callback validation and recovery from partial installation.
The Vercel button must use the pinned wheel/template and verify the database and
gateway independently; a successful frontend deployment is insufficient.

External setup still needed: a registered Supabase OAuth app, controlled HTTPS
callback domain, installer hosting and encrypted credential store, service owner,
and verified Marketplace/button integration access. Local service and adapter
failure tests can proceed before those inputs are supplied. Existing CLI access
and test-project approval do not establish these OAuth prerequisites or authorize
deployment to a new unspecified account.

Acceptance: clean-machine browser onboarding and CLI recovery finish the same
owned installation, followed by real gateway/worker inference; cancellation,
denied access and callback replay failures leave safe recoverable state.

## 6. Review and qualify the release

Run the relevant Python/Deno suites, both strict hosted probes, real-model smoke
checks, clean builds, wheel/source hash checks and isolated deployment checks.
Review the compatibility matrix and update the runbooks and release manifest
only for profiles whose declared gates actually pass. Keep unresolved profiles
experimental and record any deliberate contract change explicitly.

Acceptance: every advertised profile has evidence for its required behavior;
the release has no hidden cancellation exception, unrun installer claim or
implicit paid-tier requirement. Review the existing release workflow before a
merge to `main`, since documentation changes can also trigger it.

## Optional manual smoke test now

Manual smoke testing is available; it is not the next required release gate.
The [local runbook](experiments/local-edge-lifecycle.md#persistent-manual-testing)
contains setup and troubleshooting. With Docker, uv and the pinned Supabase CLI
available, and Ollama running with `llama3.2:3b` already installed:

```sh
uv sync --locked --extra supabase
uv run --locked python -m scripts.supabase_local start
uv run --locked python -m scripts.supabase_local apply-spike
uv run --locked python -m scripts.supabase_demo setup --model llama3.2:3b
uv run --locked python -m scripts.supabase_demo serve
```

Wait for `Local gateway ready`. In a second terminal at the repository root:

```sh
uv run --locked python -m scripts.supabase_demo doctor
uv run --locked python -m scripts.supabase_demo request
uv run --locked python -m scripts.supabase_demo request --stream
```

Expect ready checks, genuine JSON completion and NDJSON ending in `done: true`.
Press Ctrl-C in the serve terminal to stop the owned demo processes and preserve
the local stack/state. Stop the demo before Edge integration tests. This uses the
isolated local spike; it does not replace hosted qualification.
