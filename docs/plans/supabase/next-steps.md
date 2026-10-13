# Supabase next steps

Updated October 13, 2026.

The candidate is implemented and deployed to the authorized isolated Supabase
Free and Vercel Hobby targets. Both return genuine local Ollama inference, pass
route/NDJSON/private-download probes, enforce a measured 135-second synchronous
deadline, and complete async work beyond 400 seconds. The latest offline suite
has 337 passes; Deno has 44 passes. The October 12 deterministic/local selections
together cover 406 passing cases, with the known strict local Edge disconnect
failure retained. This is combined selection evidence, not one clean invocation;
real-model hosted checks passed separately.

Both hosted reader-disconnect checks fail: SQL reaches `succeeded` after the
HTTP reader closes. Neither hosting profile is qualified. See the
[hosted evidence](experiments/hosted-qualification.md) and
[full progress ledger](progress.md). Installation includes a CLI and versioned
migrations. The [local installer/adapters](experiments/local-installer.md) are
implemented and fault-tested. Actual Vercel adapter deployment, health and repeat
reconciliation pass; Edge Management-adapter deployment, hosted removal and
OAuth/button acceptance remain unrun.

The next active work completes delivery steps 6 (installation) and 7 (release
evidence) in the [implementation plan](implementation.md#11-delivery-sequence).
Step 8 API expansion/optimization is outside this work. The ordered work below
preserves the current gateway contract and experimental status.

## 1. Finish independent hosted envelope measurements

Completed on both profiles: lifetime and bounded lifecycle modes, 1,000 exact
deterministic jobs, actual Auth expiry/refresh with persisted-session acceptance,
restart/generation fencing, retry exclusion after durable output, incomplete EOF,
and a bounded slow reader. The [hosted report](experiments/hosted-qualification.md)
records the failed initial soak assertion and successful corrected `--auth-soak`
runs separately. These results do not need to be repeated as unfinished work.

Extend the bounded harness for near-limit request/JSON/NDJSON sizes, sustained
backpressure and gateway-memory observations where available, remaining hosted
adversarial/race cases, and the broader retention/maintenance envelope. Compare
relay latency with the Redis baseline before advertising performance targets.
The Edge five-reader burst hit the existing four-active-job caller budget;
Vercel's five HTTP readers do not prove five simultaneously admitted jobs.
Preserve admission limits and report their effect on the advertised concurrency.

Distinguish application deadlines from provider invocation ceilings. The observed
135-second response does not independently measure the configured 150-second
Vercel ceiling. Collect latency, SQL/RPC request counts, output/storage growth,
resource observations where available, and remaining Free/Hobby quota when the
provider exposes it. Database size alone does not establish quota headroom. Set
finite duration/concurrency/polling bounds before running; keep testing within
the existing Free/Hobby authorization, without paid upgrades or model downloads.
Paid-tier matrix cases remain unrun and cannot support a paid-profile claim.
Run hosted probes and local Edge integration tests sequentially: the hosted fake
upstream uses the same local port (55324).

Acceptance: a redacted evidence report maps each workload to the
[compatibility gates](compatibility.md), includes failed/unrun cases and cleanup,
and supports a conservative advertised time/size/concurrency envelope. Short
functional/load checks do not establish the full release envelope.

## 2. Complete fresh and repeatable packaged CLI onboarding

A clean source-archive build, installed-wheel asset/hash verification and actual
CLI doctor from an isolated uv environment and clean working directory pass.
This verifies packaging; it does not establish the entire onboarding journey.

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

## 3. Validate the remaining provider adapter operations

The adapters and ownership receipts are implemented and locally fault-tested;
Vercel's actual Hobby/account/domain checks, encrypted Production-only
configuration, clean-wheel deployment, independent health and repeated-call
reconciliation pass. Next exercise the Edge Management adapter's actual deploy/
explicit adoption and repeat recovery on an isolated owned function. Existing
CLI Edge deployment evidence does not validate that new adapter. Preserve
unrelated resources/exposure and recover lost provider acknowledgements.
Deployment remains separate from enrollment. Provider deletion must identify
owned resources and account for shared schemas, schedules and retained data.

Acceptance: repeatable installation, upgrade and recovery on the isolated targets,
with credentials absent from logs/bundles/worker files and no duplicate resources.
Test removal only against explicitly selected disposable owned resources, with
the concrete destructive operation reviewed before execution.
`uninstall` disables the cluster; `plan-edge-removal` only produces a plan.
Actual removal is exposed through the adapters' SDK methods, not a removal CLI
command. Validate interrupted and repeated removal without deleting shared data,
secrets, schedules, projects or unrelated deployments.

## 4. Supply OAuth prerequisites and validate hosted onboarding

Build on the verified operator/adapters rather than a second installation engine.
The separate single-operator service implements project selection, ownership
binding, encrypted expiring records, callback validation, durable queued setup,
CLI recovery, worker downloads and explicit inference checks. Its
[local tests and setup commands](experiments/local-installer.md) are available;
real hosted consent/onboarding remains unrun. The Vercel button must use the
pinned wheel/template and verify the database and gateway independently.

External setup still needed: a registered Supabase OAuth app, controlled HTTPS
callback domain, installer hosting and encrypted credential store, service owner,
and verified Marketplace/button integration access. The user chose local-first
validation because this setup does not yet exist. Local service and adapter
failure tests are already delivered. Existing CLI access and test-project approval
do not establish these prerequisites or authorize a new unspecified account.

Acceptance: clean-machine browser onboarding and CLI recovery finish the same
owned installation, followed by real gateway/worker inference; cancelled setup,
denied access and callback replay failures leave safe recoverable state.
Also validate real consent/refresh/revocation and the preview/ownership/security
cases in V16–V21. Publish the pinned template/button only after a tested release
artifact and hosted flow exist; the candidate wheel is not a public release.

## 5. Review the release after the required gates pass

Run the relevant Python/Deno suites, both strict hosted probes, real-model smoke
checks, clean builds, wheel/source hash checks and isolated deployment checks.
Review the compatibility matrix and update the runbooks and release manifest
only for profiles whose declared gates actually pass. Keep unresolved profiles
experimental and record any deliberate contract change explicitly.

Acceptance: every advertised profile has evidence for its required behavior;
the release has no hidden cancellation exception, unrun installer claim or
implicit paid-tier requirement. Review the existing release workflow before a
merge to `main`, since documentation changes can also trigger it.

## Open release gate: reader-disconnect cancellation

The [signal investigation](experiments/disconnect-signals.md) records signals
arriving after stream completion on both hosted providers. On October 11 the
user selected preserving the current contract and experimental status while
continuing independent work. No alternative ingress or client cancellation
protocol is the next implementation direction; this gate remains open.

Retain the strict [hosted probe](experiments/hosted-qualification.md#reproduce-the-strict-hosted-probe)
and local Edge regression:

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py \
  -k first_record --tb=short
```

Acceptance remains: an actual client closes after a nonterminal record; SQL
becomes `cancelled`, the worker stops its upstream read, and the gateway releases
reader/RPC resources within the tested bound. A normally completed response is
not cancelled; accepted async work survives submission disconnect. Do not skip,
weaken or replace the failing assertion with mocked evidence. Local persistent
FastAPI cancellation does not qualify Edge or Vercel. Completing the independent
work above cannot close this gate or qualify either profile by itself.

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
