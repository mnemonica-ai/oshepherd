# Supabase next steps

Updated October 14, 2026.

**Priority: deliver the adoption-first CLI experience.** The user's primary journey
is one uv entry to provision/connect cloud resources and share local Ollama, plus a
simple entry to join another machine. Follow the new [CLI onboarding plan](cli-onboarding.md)
before hosted OAuth/button setup or further benchmark expansion. The candidate
operator workflow is the engine for this work, not the finished user experience.

## 0. Deliver share and join

Continue the ordered A1–A5 slices in the [CLI onboarding plan](cli-onboarding.md).
The source candidate now includes portable worker invitation/join, resumable share
with worker/cleanup supervision and guided existing Supabase Edge project setup.
See the [user guide](../../share-ollama.md) and current evidence in the ledger.
The user authorized an orchestrator with implementation subagents.

Next implement fresh provider project creation with durable reconciliation and
verified free-plan constraints, guided optional Vercel setup, removal of the
database URL/psql prerequisite, optional service management, and published-package
fresh-user acceptance. Hosted OAuth remains a later distribution path.

The finished acceptance is one uv entry from outside the checkout to authenticated
real inference, and another machine joining with no administrative credentials.
The existing-project wizard and prepared-configuration path do not meet that
complete promise. Supabase is required; Vercel is optional.
Retain experimental labeling and the open cancellation gate throughout.

## Existing evidence and remaining qualification

Independent implementation and acceptance work is delivered: the local installer,
owned deployment/removal adapters, installed-wheel local CLI lifecycle, and
bounded hosted load/Auth/recovery/request/output probes. The
[October 14 report](experiments/independent-acceptance.md) records the two fixes
found during acceptance: source paths for strict Edge adoption, and finite upload
tail draining for prompt oversized-request rejection.

The adoption slice's latest offline suite has **454 passes**; focused local
operator/maintenance/worker integration has **24 passes** and installed-wheel
share/invite/join with real Ollama passes. See [CLI acceptance](experiments/cli-onboarding.md).
Deno's unchanged prior evidence has **48 passes**. The previous full combined
deterministic/local selection has **429 passes, one known strict Edge
disconnect failure and two model cases deselected**; it predates the CLI additions.
Both final hosted strict probes still fail reader
disconnect cancellation, so neither profile is qualified.

**The user-approved removal tests are complete. Both hosted test clusters are
disabled and their reviewed gateway resources removed.** Projects, retained
data, shared schedules, Edge secrets and Vercel Production configuration remain.
Old application keys/worker sessions are revoked. More hosted testing requires
new explicitly selected disposable installations; do not reuse removed receipts
or overwrite preserved project-wide secrets for a new cluster blindly.

## Completed independent work

- Both profiles: 1,000 exact jobs, actual Auth expiry/refresh with saved-session
  acceptance, restart/generation fencing, retry exclusion after durable output,
  incomplete EOF, explicit cancellation, expiry/maintenance and bounded slow reads.
- Both profiles: near-limit requests, oversized rejection without admission,
  exact JSON at response budgets and multi-page/chunk NDJSON with a paused reader.
  The final Edge five-MiB rejection is 413 in 0.468 seconds; earlier timeout
  reports remain separate.
- Current installed wheel from a clean uv environment: configure, repeated
  install/upgrade/enrollment, login, CLI API/worker startup, doctor, real JSON and
  terminal NDJSON inference, restart/generation advance, rotation, revocation
  and repeated disablement on a new cluster in the existing isolated local stack.
- Actual Edge adapter adoption, Management API upgrade, health and repeat
  reconciliation; actual Vercel adapter deployment/reconciliation; approved
  removal and repeat recovery on both providers, including labelled simulated
  acknowledgement loss after a real DELETE succeeded.
- Local OAuth service/fault validation, encrypted expiring records, durable
  shared-engine setup/recovery, private worker exports and explicit inference
  checks. Hosted OAuth/button setup does not yet exist.

These close the tested independent portions of delivery steps 6–7 in the
[implementation plan](implementation.md#11-delivery-sequence). They do not close
the full installation matrix or release qualification. Step 8 API expansion/
optimization and alternative ingress/cancellation protocols remain outside this
work.

## 1. Defer optional hosted OAuth onboarding until CLI adoption is delivered

The user reconfirmed local-first validation on October 14 and subsequently made
CLI adoption the priority. These inputs do not block CLI implementation. External setup remains:
a registered Supabase OAuth app, controlled HTTPS callback domain, a POSIX
installer host/private durable credential store and maintenance owner, and
verified Marketplace/button integration access. The
[installer runbook](experiments/local-installer.md) gives the implemented service's
commands; there is no placeholder public button.

Once those inputs and explicit disposable targets are available, test real
consent/refresh/revocation, HTTPS callbacks, ownership/denial/replay, browser
restart, queued installation recovery and private worker onboarding. Browser and
CLI must finish the same owned installation, then return genuine gateway/worker
inference. Measure cloud readiness and first inference separately.

Acceptance: hosted installation cases V16–V21 pass; a published pinned release/
template/button uses tested artifacts and independently verifies database,
gateway and worker readiness. The candidate wheel is not a public release.
CLI authentication alone does not establish OAuth prerequisites.

## 2. Complete fresh hosted/project and upgrade acceptance

The full packaged CLI lifecycle passed locally on the existing isolated project.
It does not prove a new empty hosted project, real browser onboarding, or every
legacy/overlapping-version upgrade scenario.

Use the shared engine and [operator runbook](experiments/operator-lifecycle.md)
on newly selected disposable installations. Cover fresh project setup,
interrupted/resumed upgrades, unchanged identities, drift rejection, ownership,
preview isolation, credential rotation/revocation and rollback. Physical adapter
removal is already accepted; `uninstall` still means cluster disablement, not
provider project/data deletion.

Acceptance: V16–V21 are mapped to actual evidence, including failures and unrun
cases; no hidden development state, administrator credentials on workers, or
production credentials in previews.

## 3. Finish the broader performance/resource envelope

The bounded size, load and slow-reader subset passes; sustained server
backpressure, hosted gateway memory, Redis-relative relay latency, provider
invocation ceilings and remaining account quota have not been established.
Extend reproducible measurements only on explicitly selected new test resources.

Preserve the four-active-job caller limit and enrolled worker capacity. Five
overlapping HTTP readers do not prove five admitted jobs; the rejected Edge
burst remains evidence. Compare with Redis before advertising latency targets,
measure SQL/RPC and storage/egress cost, and record provider resource/quota
observations where exposed. Database size is not quota headroom. The measured
135-second application deadline does not independently establish Vercel's
150-second invocation ceiling.

Set finite time/concurrency/polling limits, retain failed reports and cleanup,
and keep the existing Free/Hobby-only scope. Paid cases remain unrun. Use distinct
fake-upstream ports or run hosted/local Edge probes sequentially.

Acceptance: a conservative advertised time/size/concurrency/cost envelope is
supported by the [compatibility gates](compatibility.md), with unknown resource
measurements explicitly identified.

## 4. Review release readiness after the required gates pass

Run relevant Python/Deno suites, strict hosted probes, real-model smoke checks,
clean builds and installed-wheel/source hash checks for the chosen fresh targets.
Update the matrix/runbooks/manifest only from actual results. Keep unresolved
profiles experimental. Review the release workflow before merging to `main`,
since documentation changes can also trigger publishing.

Acceptance: every advertised profile satisfies its declared contract and
installation requirements; there is no hidden cancellation exception, unrun
button claim or implicit paid-tier requirement.

## Open release gate: reader-disconnect cancellation

Both final strict hosted probes still observe SQL `succeeded` after the HTTP
reader closes. The [signal investigation](experiments/disconnect-signals.md)
records late delivery on both providers. The user preserved the current contract
and experimental status; an alternative ingress/client protocol is not the next
implementation direction.

Retain the strict assertion and local regression:

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py \
  -k first_record --tb=short
```

Acceptance remains: an actual reader closes after a nonterminal record; SQL
becomes `cancelled`, the worker stops its upstream read and reader/RPC resources
are released within the tested bound. Normal completion is not cancelled;
accepted async work survives submission disconnect. Do not weaken or substitute
mocked evidence for this gate. Persistent FastAPI cancellation and completing
independent work cannot qualify Edge or Vercel by themselves.

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
