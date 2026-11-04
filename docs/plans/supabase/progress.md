# Supabase implementation progress

Updated October 31, 2026. The objective is to finish the implementation so
an Ollama client can send a request to an Edge Function and receive inference
from a local Ollama model. This ledger records authoritative artifacts and
verification; an implementation checkbox does not imply hosted validation.

The local testing milestone is achieved: a real Edge Function request reaches
the external worker and returns genuine Ollama inference. The complete handoff
is still unfinished; production qualification, the broader hosted envelope,
installation acceptance and proxy disconnect gates remain open below. New Python
work uses uv by default;
older verification-log commands retain their original invocation.

Continue with the [adoption-first CLI milestone](cli-onboarding.md) and
[ordered next steps](next-steps.md). The user prioritized one uv entry to share
Ollama and join another machine over hosted OAuth/buttons and more benchmarks,
while preserving the current contract and experimental status. Implementation
subagents are explicitly authorized under one orchestrator.

October 14 independent acceptance is recorded in the
[current report](experiments/independent-acceptance.md). Both approved hosted
gateway removals are complete and their clusters are disabled; retained data and
shared provider configuration remain. Hosted inference needs a new explicitly
selected installation; retired keys/sessions cannot be reused.

## October 31 adoption extension: implementation and provisional evidence

The unpublished candidate is `0.0.25a1`. Supabase setup can create/select an
organization/project, discover regions, verify the optional organization plan
field, reject non-Free plans, and record explicit attestation if the field is
absent. Durable creation records precede POSTs; uncertain outcomes reconcile
without duplicate creation. Management SQL shares the migration/schedule engine
and preserves the existing private-database operator route. Vercel setup selects
Hobby resources and deploys verified installed bytes through REST. User services
support foreground-established share/join state with private receipts and optional
owned durable uv runtimes.

Provider/provisioning contract tests passed **38 cases**. Candidate archive gates
passed **17 cases**, including the one-byte `.gitignore` marker produced by actual
`uv build`. An actual `0.0.25a1` sdist/wheel pair passed source/manifest/hash checks
and isolated uv installation outside the checkout. Installed `oshepherd`, `share`,
`join`, `invite`, `service` and `supabase` help entry points passed with no cloud
credentials. This verifies packaging/entry points, not fresh hosted inference.
The [distribution report](experiments/distribution.md) explains the explicit
publication gate and external prerequisites.

CI compatibility was checked with the actual pinned **uv 0.9.18**: it reads the
current lockfile revision 5 with `--locked --offline`, and its full/runtime exports
match the checked-in exports (ignoring the full export's generated command header).
No uv pin change was required. The supported **Deno 1.41.0** passed type checking
and **48 Edge contracts**; system Deno 2.9.8 type errors are not evidence for the
pinned runtime. No Edge source changes were required.

Fresh Vercel Hobby project creation, provider ownership/domain selection and REST
readiness have creation evidence only. The project has no deployment or environment
entries, and a hosted Vercel function cannot reach a local Supabase stack.

### Local-first validation of candidate r3

The user deferred hosted Supabase and directed all testing to local Supabase
containers first. Nothing below touched a hosted Supabase project or a hosted
gateway. Commands and private evidence paths are in the
[acceptance report](experiments/cli-onboarding.md#october-31-local-first-acceptance).

| Check | Result |
| --- | --- |
| Offline contracts and SystemOne | **564 passed** in 14.70 seconds |
| Pinned Deno 1.41.0 | Type check and **48 Edge contracts** passed |
| Local Supabase integration, full selection | **69 passed, 1 failed, 2 deselected**, three consecutive runs; the failure is the known strict Edge disconnect gate |
| r3 wheel, real macOS LaunchAgents | Passed, including foreground share/invite/join, both services' JSON and NDJSON, restart generations and preserved worker state |
| r3 wheel, real Linux user systemd 252 | The same harness passed in a Debian 12 container, with the injected uninstall faults below |
| Management SQL, fresh disposable stack | Passed as non-superuser `postgres`, with limits below |

Real systemd and independent review found defects that mocks had hidden:

- **Every Linux service failed to load.** `WorkingDirectory=` was quoted, and
  systemd rejects a quoted value as not absolute, so each install rolled back.
- Linux uninstall could not recover after a failed `daemon-reload`, because retry
  ran `disable` on the removed unit. systemd exits 1 for that. With the reload
  failure injected on real systemd, retry now recovers and preserves the worker
  session.
- An install interrupted between its receipt and unit stranded the service name.
- A partial owned runtime directory blocked reinstallation; `uv venv` now clears it.
- The publish job's OIDC token was exposed to dependency code during reverification;
  reverification moved to a job without that permission. This workflow change has
  not run on GitHub.

Limits of this evidence:

- Management SQL replaced api.supabase.com with real `postgres-meta`. It proves
  transactions, lost-acknowledgement recovery, repeat reconciliation and schedules
  on real Postgres, then a real PostgREST/Auth bootstrap. It does not prove hosted
  endpoint response or error shapes, which remain unobserved.
- Local `postgres` lacks the Storage grant option that earlier hosted installs had,
  so the run first proved the privilege gate rejects before mutation, then granted
  that option to approximate the hosted role. A newly created hosted project's role
  is unverified, and this check would not reveal a difference there.
- The Linux run is a container on Docker Desktop with lingering enabled, reaching
  the host's stack and Ollama through loopback forwards. It is not a physical Linux
  login session.
- Fresh project creation, live provider APIs, hosted inference and Vercel deployment
  are not covered by local containers.

With the privileged systemd container running, all four full integration runs had
one extra Edge failure, a 503 or a dropped connection. All three full runs with it
stopped were clean. Edge-only runs mostly passed with it up, and the failing test
varied, so this is a small-sample correlation with no established mechanism. Do
not run the two together.

Historical October 14 totals below do not count this work.

## Completed

- [x] Reprioritize the roadmap around share/join and create-or-select provider setup;
  document A1–A5 acceptance and distinguish the operator engine from the user journey.
- [x] Implement source `share`, `invite` and `join`, guided existing Supabase Edge
  setup, restricted portable worker invitations and foreground worker/cleanup
  supervision. Verification and limitations are recorded below.
- [x] Implement October 31 provider creation/selection, optional Vercel REST setup,
  Management SQL without database URL/psql input, optional macOS/Linux user services,
  and a gated experimental candidate build/publication workflow. Implementation
  is distinct from the integrated/live acceptance recorded below.
- [ ] Complete A1–A5 acceptance: finish fresh hosted validation, publish an
  explicitly approved candidate after external release setup, and validate its
  pinned package on clean user machines. Local service-manager acceptance passed
  on macOS and containerized Linux systemd.

- [x] Add bounded hosted request/JSON/NDJSON envelope checks and validate both
  profiles. Fix Edge upload-tail rejection and source-relative Management adapter
  bundling without changing inference cancellation or applied SQL.
- [x] Validate the complete current installed-wheel local CLI lifecycle, including
  genuine Ollama inference, generation advancement, rotation and repeated disablement.
- [x] Pass actual Edge adoption/upgrade/repeat reconciliation and user-approved
  removal/recovery on both providers; verify retained jobs/schema/schedules,
  Edge secret digests and Vercel encrypted Production-only configuration.
- [x] Implement and locally validate the separate OAuth installer and owned
  provider adapters; verify the Vercel adapter on the authorized Hobby target.
  Hosted OAuth/button gates remain unrun; later Edge/removal acceptance is above.
- [x] Run 1,000 exact deterministic jobs on each hosted profile and bounded
  restart/fencing, incomplete EOF and slow-reader probes. Record four-active-job
  caller admission and the rejected Edge five-request burst without changing
  limits. Both corrected actual Auth expiry/refresh probes pass, including old
  JWT rejection, saved new-session acceptance and inference after expiry.

- [x] Add and run `--bounded-lifecycle` against both hosted profiles: four-job
  queueing/capacity-two enforcement, explicit cancellation stopping the worker,
  silent-upstream deadline with independent metadata/heartbeat, signed/content
  expiry and actual cluster-scoped expired-object cleanup. Respect the five-minute
  deletion quarantine; guard against active installations before worker startup.
  Both mode runs pass. This functional subset does not close soak/load/disconnect
  qualification; see the [hosted report](experiments/hosted-qualification.md).
- [x] Trace actual hosted disconnect signals with bounded event-only diagnostics:
  Edge delivers abort after stream completion and lacks handler completion info;
  Vercel ASGI receives disconnect after the final body. Restore both normal
  gateways and remove temporary diagnostic cloud objects/function. Retain strict
  failing gates; see the [signal report](experiments/disconnect-signals.md).
- [x] Promote lifetime measurement into checked-in `scripts.supabase_hosted_check`
  `--lifetimes`, with bounded delays/polling, owned-job cancellation on failure,
  foreign status-URL rejection and cancellation-acknowledgement-loss regressions.
  Both hosted profiles pass this mode; it does not waive disconnect qualification.
- [x] Create the explicitly approved isolated Supabase organization/project with
  the default Free plan, install on PostgreSQL 17.11, deploy Edge, enroll/login
  an actual CLI worker, and return genuine local Ollama inference. Hosted route
  fixtures, exact NDJSON, async replay and a 1.2 MB private download pass. The
  strict hosted Edge disconnect check fails and remains a release blocker.
  See [hosted qualification](experiments/hosted-qualification.md).
- [x] Deploy the approved isolated Vercel Hobby candidate with eight encrypted,
  Production-only variables. Real Ollama inference, route fixtures, exact NDJSON
  and a signed 4.2 MB private download pass. Both providers enforce the 135-second
  synchronous budget, while async completions survive beyond 400 seconds.
  Vercel's strict reader-disconnect check also fails; both profiles remain unqualified.
- [x] Fix the hosted Storage compatibility and masked PostgreSQL error findings.
  Preserve applied migration bytes, select a compatible fresh baseline or the
  legacy upgrade path from the manifest, retain authoritative object checks,
  detect trigger/permission drift, and require Storage schema grant options.
  Local configure uses the isolated administrator role. Add a repeatable hosted
  qualification probe with an unsuccessful exit for failed cancellation.
- [x] Reproduce the versioned Storage commit race with a real two-session test,
  then fence non-key archive/metadata updates with an additive `FOR SHARE` lock
  migration. Apply it to both isolated hosted installations and preserve earlier
  migration bytes.
- [x] Add independent versioned release-candidate migrations, server-only
  operator RPCs, protected install/enrollment/doctor/upgrade/rotation/disable CLI,
  migration history and catalog drift checks. Verify replay, lost responses,
  ownership, one-use Auth, key recovery, grants and schedule isolation locally.
  See the [operator lifecycle](experiments/operator-lifecycle.md).
- [x] Ship verified wheel-contained assets and a self-contained Vercel candidate
  template with Python source/artifact hashes, exact runtime pins, uv lock,
  150-second invocation configuration and the existing 135-second SQL budget.
  Clean-cache export/install/import and default auth/preview denial passed.
- [x] Isolate bounded metadata from inference connections and validate inventory.
  Eleven deterministic recovery/control cases pass, including actual socket
  saturation, Auth refresh, restart fencing and a blocked ASGI reader. See
  [worker recovery evidence](experiments/worker-recovery.md). Subsequent load/Auth
  and bounded slow-reader evidence is recorded below; sustained server-side
  backpressure remains unverified.
- [x] Fix the local startup helper to retrieve explicit status JSON after the
  human-readable start summary. Preserve private logs and existing credentials
  on failures. Verify the user's exact command against the running isolated
  stack and add six CLI regression cases.
- [x] Fix the October 7 review defects: bound complete claim pages to the 2 MiB
  RPC response limit before assignment; store claim receipts without payload
  copies and hydrate authorized live replay; upgrade existing receipts; persist
  queue-wait budgets and renew retry queue deadlines without extending total
  execution. Add real SQL/Auth and worker regressions covering Unicode/escaping,
  near-limit requests, mixed pages, receipt upgrades/expiry, backoff, fencing and
  retry exclusions. Correct current uv and test setup guidance. Remove the
  completed review patch plan; preserve its original evidence in commit
  `acbb9b1` and unresolved investigations below.
- [x] Standardize project setup, commands, builds and CI on uv. Add
  `.python-version`, `uv.lock`, a default development group and explicit isolated
  build backend. Keep Conda optional as a Python provider and generate
  `requirements.txt` from the lock. Verify release version bumps update the lock
  without changing dependency pins. Local Python is now uv-managed 3.12.12.
- [x] Characterize C01–C09 in 67 language-neutral route fixtures, including
  defaults, null/empty values, errors, metadata and exact NDJSON serialization.
- [x] Export and verify five request payload schemas.
- [x] Add a controllable fake Ollama upstream supporting delays, silence,
  fragmentation, abrupt/incomplete EOF, cancellation and large output.
- [x] Characterize the real Ollama SDK/Celery worker boundary.
- [x] Add deterministic CI and development dependencies in both metadata files.
- [x] Exclude worker sessions, local CLI state and test services from packages.
- [x] Verify the initial slice: 162 tests; clean-source sdist/wheel checks.
  [Commands and evidence](experiments/step-0-local-characterization.md).
- [x] Verify the local Docker daemon is available (29.8.1) and inventory
  existing containers before creating isolated test resources.
- [x] Extract broker-neutral request normalization and Ollama execution into
  `common/protocol.py` and `worker/executor.py`; keep the existing SDK/Redis
  semantics in a legacy executor adapter. New async execution bounds output,
  rejects incomplete EOF and explicitly selects upstream stream mode.
- [x] Defer Redis/Celery imports until existing CLI commands actually start;
  verify CLI/executor import without either backend module initialized.
- [x] Fix inherited environment-field loading and omit secret inputs/context
  from configuration-validation logs.
- [x] Add the Supabase extra to both package metadata files, pinning the tested
  HTTPX/Ollama transport versions.
- [x] Start the separate local Supabase stack, with project ID
  `oshepherd-development` and API/database ports 55321/55322.
- [x] Add bounded RPC retries that preserve operation IDs, backend-specific
  worker configuration, private atomic session storage and Auth exchange/refresh.
- [x] Verify browserless operator-generated enrollment against real local Auth:
  public signup denied, one-time exchange, refresh and 0600 session storage;
  both modern secret and legacy service-role key administrative flows passed.
- [x] Write experimental schemas for 19 RPCs and async submission/status/download
  formats, including fixtures for all six states and content expiry.
- [x] Install isolated SQL identities, hashed application keys, worker memberships,
  nonce-preserving registration and generation fencing. Real Auth/PostgREST
  tests verify tenant isolation, denied grants, replay and worker revocation.
- [x] Reach local Ollama 0.35.0 and inspect installed models; select
  `llama3.2:3b` for the bounded generation lifecycle test.
- [x] Implement the original 17 experimental RPCs in the isolated logged queue: quotas,
  routing, leases, attempt fencing, checksum/replay, atomic completion,
  bounded retry, cancellation, inline results and retained tombstones.
- [x] Implement the external async worker with independent inventory, heartbeat,
  refresh and inference tasks, final-record withholding and output limits.
- [x] Verify all four inference operations and exact SDK NDJSON through real
  Auth/PostgREST; prove control tasks continue during inference silence.
- [x] Implement nine Edge routes and async admission/status/inline result APIs.
  Verify 27 normalization fixtures, 42 Deno tests and real local Edge routes.
- [x] Verify real local Edge → SQL → external worker → Ollama inference:
  `llama3.2:3b`, `done: true`, six tokens, 626 response bytes.
- [x] Add worker backend selection (Redis default), private enrollment-file login
  and startup/refresh composition without importing Celery or Redis.
- [x] Add private Storage policies for current attempts, immutable uploads,
  verified upload recovery and authoritative missing-object rejection. The
  original local spike uses a Storage foreign key without runtime reads; the
  versioned candidate now uses the compatible commit trigger and scoped reads.
- [x] Verify real Edge large-result behavior: job succeeds, JSON retrieval
  returns 413, authorized signed download returns exact bytes/checksum, and
  another application key receives 404. Async jobs store private result objects.
- [x] Add resumable persistent local setup, supervisor, doctor and bounded
  JSON/NDJSON request commands in `scripts/supabase_demo.py`. Repeated setup
  preserves identities, operation IDs, application key and worker session.
  Actual worker CLI startup/restart and both real-model response modes passed.
- [x] Bound Python RPC response reads to 2 MiB and ten seconds per attempt;
  bound Auth responses and report ambiguous single-use exchanges without retry.
  Share atomic private-file handling with bounded reads and ancestor checks.
- [x] Apply all six local spike SQL files in one transaction; a failed apply
  rolls back schema and grant changes together. Reject non-terminal final stream
  records before successful SQL completion.
- [x] Add a backend-aware API factory and `start-api --backend supabase`, with
  Redis remaining the default. FastAPI serves all nine routes and async APIs,
  checks private result sizes/checksums, signs authorized downloads and owns its
  readers/HTTP clients per request. Construction performs no network I/O.
- [x] Verify optional FastAPI through real Auth/SQL/worker/Storage and an actual
  HTTP socket disconnect: the job cancels and the worker read stops. Add a
  resource-free Vercel ASGI factory with explicit backend/profile and preview
  guards; published template and hosted qualification remain pending.
- [x] Persist private Storage deletion intent before purging references;
  bound chunk and worker receipt cleanup, retaining submission tombstones.
  Add cluster-scoped indexed orphan scans, expiring deletion claims and
  replayable acknowledgement receipts with authoritative metadata checks.
- [x] Add `supabase-maintain`, an independent operator runner, and supervise
  maintenance alongside local worker/Edge. Verify real Storage deletion,
  lost acknowledgements, stale claims and live/committed/cross-cluster protection.
- [x] Add private bounded round-robin SQL sweeps and explicit local Cron
  scheduling/removal with command collision checks. Real scheduled expiry/purge
  passed without worker/gateway processes; repeated scheduling reused job IDs.
  See [maintenance evidence](experiments/local-maintenance.md).

## Implementation and lifecycle gates

| Work | Implementation | Verification needed |
| --- | --- | --- |
| Versioned RPC, retry, Auth, limits, output and async contracts | Schemas/decisions written | Enforce in SQL and compare Python/TypeScript behavior |
| Shared executor and backend-specific startup/configuration | Executor/config/RPC/Auth and API/worker CLI selection implemented | Redis characterization remains passing; full error matrix pending |
| Isolated local Supabase configuration and pinned toolchain | Local stack/Auth/Edge running | Real model lifecycle passed; disconnect/hosted limits open |
| Database schema, RPC grants/RLS, capacity, leases and fencing | Spike plus independent versioned candidate migrations implemented | Local history/drift, identity, quota, races, replay/fencing passed; hosted and full adversarial matrix pending |
| Worker enrollment/session storage and refresh | Auth/storage/membership/login and operator enrollment implemented | Local recovery/refresh, actual hosted enrollment/CLI login and corrected actual hosted Auth expiry/refresh passed; broader adversarial matrix pending |
| External local Ollama worker and independent heartbeat | Implemented with separate bounded metadata pool | Fake/real model, hosted restart/fencing, capacity-two load and actual Auth-cycle control/metadata passed; broader drain/extended envelope pending |
| All nine Edge routes and application-key authentication | Implemented | Local and hosted route fixtures/auth/metadata passed; full adversarial matrix pending |
| Durable streaming, cancellation and output limits | Implemented in SQL/worker/gateway | Exact bytes, local FastAPI cancellation, controlled ASGI stall, bounded hosted size/slow reader and 1,000-job loads passed; **local Edge and both hosted reader-disconnect checks fail**; sustained backpressure/gateway memory pending |
| Async jobs, expiry, cancellation and authorized results/downloads | Inline/private-object paths implemented | Edge/FastAPI async and downloads passed; full expiry/adversarial matrix pending |
| Private Storage results and cleanup/reconciliation | Upload/sign/read, deletion outbox, orphan reconciliation, operator runner and private Cron sweeps implemented | Local authorization/recovery/purge/Cron and bounded hosted signed/content expiry plus actual expired-object cleanup passed; broader hosted retention/maintenance envelope pending |
| Optional FastAPI backend and Vercel template | Backend-aware API and verified wheel-backed template implemented; approved test deployment now removed | Hosted real model/routes/download, 135-second application limit, response-budget JSON and 1,000-job load passed; disconnect fails; platform ceiling and broader resource envelope pending |
| CLI bootstrap/doctor and operator administration | Protected candidate configure/install/enroll/doctor/upgrade/rotate/revoke/disable implemented | Full installed-wheel local CLI lifecycle and actual hosted removal passed; fresh hosted/browser onboarding remains unrun |
| Installation manifest, OAuth installer and provider adapters | Hash-verified manifest, schema exposure, separate OAuth control service and owned Edge/Vercel adapters implemented | Local resumability/fault tests, actual Edge/Vercel deployment/reconciliation and reviewed live removal passed; hosted OAuth/Marketplace/button acceptance pending |
| Runbook and automated Edge → worker → response harness | Local and [operator runbooks](experiments/operator-lifecycle.md), disposable two-schema harness available | Real inference passed in both schemas; complete hosted clean-machine setup pending |
| Hosted limits, load/cost, lifecycle and installation | Isolated Supabase project deployed on default Free plan; Vercel Hobby verified and approved Production configuration deployed | Both real inference/routes/download, 1,000-job loads, corrected actual Auth expiry and recovery pass; sync deadlines and async jobs beyond 400 seconds measured; disconnect fails; broader envelope/quota and installation acceptance pending |

## Current decisions and remaining dependencies

Redis remains the default. Supabase defaults to Edge and keeps async and optional
FastAPI in scope. There is no automatic cross-backend retry. Production migrations
must follow a reviewed, versioned protocol; temporary spike SQL stays separate.

Worker enrollment will be operator-provisioned Supabase Auth identities with
membership/capacity bound in SQL. The proposed browserless exchange uses an
administrator-generated, single-use Auth token followed by the worker's direct
Auth verification. This passed against local Auth with public signup disabled.
No worker receives a project administrator credential.

Free-tier-only hosted testing is authorized, and isolated targets/authentication
are selected. The new Supabase organization/project used the documented default
Free plan, without paid flags or upgrade. Vercel Hobby was independently verified
through its team API. Automatic approval review initially rejected uploading the
isolated Supabase server secret to Vercel Production because that payload and
destination needed explicit approval. The user supplied that approval, and
eight variables were verified encrypted and Production-only in the exact Hobby
test project. No database password or worker session was uploaded. Real Ollama
uses the existing installed model.

Local Edge Runtime `v1.74.3` did not propagate an HTTP reader disconnect to SQL
cancellation: the job succeeded. The strict regression test remains failing.
Request abort, stream cancel and optional response-completion hooks are
implemented, but do not establish end-to-end cancellation through this proxy.
The finite execution deadline still bounds work. This gate stays open. On
October 7 the strict real-runtime test again observed `succeeded` instead of
`cancelled`, both alone and in the full integration selection. Investigate
runtime/proxy abort propagation and a supported response lifecycle hook with
this test; do not replace it with direct Deno or FastAPI evidence. If the runtime
cannot supply a disconnect signal, establish a supported deployment/protocol
decision before claiming the gate is closed. The existing strict assertion stays
in place. October 11 hosted deployments reproduce the failure on both Edge and
Vercel Python. The hosted report records these measurements and Vercel's
Node.js-only documented cancellation opt-in.
The later [signal investigation](experiments/disconnect-signals.md) observes
the cleanup signal arriving after stream completion on both deployments. No
transport/deployment contract change has been made; independent qualification
continues. The user explicitly selected preservation of the existing contract
and experimental status on October 11. Both strict cancellation gates remain
open; no alternative ingress/client protocol is part of the current work.

The October 7 review run also observed a 503 at initial async admission in
`test_real_edge_large_success_requires_authorized_download`; its focused rerun
passed. After the R1–R4 patches, that test and the other 44 passing local
integration cases passed in the full selection, without reproducing the 503.
Its cause remains unestablished. If it recurs, inspect only safe response error
codes and protected runtime diagnostics, and distinguish function readiness,
runtime restarts, SQL admission and transport errors. Do not add blind inference
retries or treat a single successful rerun as proof of a fix.

The local lifecycle is ready for manual testing against the already installed
`llama3.2:3b`. Run `uv run --locked python -m scripts.supabase_demo serve`, then
run `uv run --locked python -m scripts.supabase_demo doctor` and
`uv run --locked python -m scripts.supabase_demo request` from another terminal.
The supervisor was stopped after verification;
the Supabase stack, demo identities/configuration and user's Ollama remain.
This readiness milestone does not close the remaining production/installation
gates above. Experimental SQL currently caps synchronous work at 135 seconds
for both gateway implementations, including the Vercel profile.

## Verification log

| Date | Command/evidence | Result |
| --- | --- | --- |
| October 4, 2026 | `python -m pytest -q tests/contracts tests/test_systemone.py` | 162 passed; initial slice |
| October 4, 2026 | Clean-source archive and wheel inspection | Tests, environment files and worker state excluded |
| October 4, 2026 | `docker info --format '{{.ServerVersion}}'` | 29.8.1; daemon available |
| October 4, 2026 | `python -m pytest -q tests/contracts tests/test_systemone.py` | 228 passed; executor, transport and protocol contracts |
| October 4, 2026 | `python -m pytest -q tests/integration/supabase` | 3 passed; real Auth and identity/session SQL |
| October 5, 2026 | `python -m pytest -q tests/contracts tests/test_systemone.py` | 239 passed |
| October 5, 2026 | `deno test --allow-read=protocol supabase/functions/ollama/gateway_test.ts` | 42 passed |
| October 5, 2026 | Real local Auth/SQL/external-worker suite before Edge additions | 15 passed |
| October 5, 2026 | Real Edge route and async lifecycle test | Passed |
| October 5, 2026 | Real Edge first-record/disconnect test | Early record passed; **SQL cancellation assertion failed** |
| October 5, 2026 | Real Edge `llama3.2:3b` smoke test | 1 passed in 9.46s; six tokens, 626 response bytes |
| October 5, 2026 | Python service-free suite after upload recovery test | 240 passed |
| October 5, 2026 | Real Storage authorization/commit suite | 2 passed in 12.29s |
| October 5, 2026 | Real Edge routes/async and large successful download | 2 passed in 12.73s |
| October 5, 2026 | Queue/Storage/Edge tests after atomic SQL apply and terminal-record validation | 8 passed in 25.05s |
| October 5, 2026 | `python -m pytest -q tests/integration/supabase -k 'not first_record and not local_ollama_model' --tb=short` before FastAPI addition | 19 passed in 33.74s; known disconnect gate explicitly deselected |
| October 5, 2026 | Persistent `setup` twice, `serve`, `doctor`, `request` and restart plus `request --stream` | Same install resumed; ready doctor; real JSON completion and nine NDJSON records ending with genuine `done: true`, eight tokens |
| October 5, 2026 | `python -m pytest -q tests/integration/supabase -k fastapi --tb=short` | 2 passed in 9.26s; nine routes/async private download plus real socket cancellation |
| October 5, 2026 | Latest `python -m pytest -q tests/contracts tests/test_systemone.py --tb=short` | 268 passed in 12.24s |
| October 5, 2026 | Latest Deno gateway contract suite | 42 passed |
| October 5, 2026 | Clean-source sdist and rebuilt wheel inspection after new gateway modules | Runtime modules present; private state, tests and checkout scripts excluded |
| October 5, 2026 | Combined real local suite after FastAPI addition, excluding the separately recorded proxy-disconnect and opt-in model tests | 21 passed in 40.30s; two explicitly deselected |
| October 5, 2026 | Local setup recovery/private-file/journal contract subset after strict journal loading | 4 passed; incomplete state cannot silently regenerate install IDs |
| October 6, 2026 | `python -m pytest -q tests/contracts tests/test_systemone.py --tb=short` | 276 passed in 15.07s |
| October 6, 2026 | `python -m pytest -q tests/integration/supabase -k maintenance --tb=short` | 4 passed in 33.88s; real deletion recovery/isolation, bounded stream purge and actual Cron |
| October 6, 2026 | Local `schedule-cleanup` twice, Cron run history, then `unschedule-cleanup` | Same named jobs reused; reaper and purge succeeded; only owned schedules removed |
| October 6, 2026 | Three-process demo `serve`, `doctor`, `request`, `request --stream`, Ctrl-C | Ready; real JSON success plus nine genuine NDJSON records/eight tokens; owned processes stopped |
| October 6, 2026 | Actual `supabase-maintain --once` with temporary private operator env file | Passed; aggregate counters only; temporary credential file removed |
| October 6, 2026 | Rebuilt sdist/wheel archive inspection | Maintenance runtime included; private state, tests and checkout scripts excluded |
| October 6, 2026 | Combined real local integration suite after maintenance, excluding recorded proxy-disconnect and opt-in model tests | 25 passed in 139.56s; two explicitly deselected; real model verified separately |
| October 6, 2026 | Real Cron scheduling collision regression | 1 passed in 1.09s; same job ID on replay; unexpected command preserved on install/removal attempts |
| October 6, 2026 | `uv lock --check`, locked sync and actual CLI entry point | Passed; previous dependency versions preserved; uv-managed Python 3.12.12, Conda not required |
| October 6, 2026 | `uv run --locked pytest -q tests/contracts tests/test_systemone.py --tb=short` on uv-managed Python | 276 passed in 12.38s; one upstream TestClient deprecation warning |
| October 6, 2026 | `uv build` plus archive inspection and temporary-copy release bump/lock/export check | Passed; clean sdist/wheel; dependency pins and compatibility export unchanged by version-only bump |
| October 6, 2026 | `OSHEPHERD_REAL_OLLAMA_MODEL=llama3.2:3b uv run --locked pytest -q tests/integration/supabase/test_edge.py -k local_ollama_model --tb=short` | 1 passed in 13.03s; real local Edge/SQL/worker/Ollama response on the uv environment |
| October 7, 2026 | New review regressions against baseline SQL, using `uv run --locked pytest -q tests/integration/supabase/test_review_regressions.py --tb=short` | 9 failed, 5 passed in 9.60s; reproduced claim-size, payload receipt and retry-deadline defects before applying patched SQL |
| October 7, 2026 | `uv run --locked python -m scripts.supabase_local apply-spike` | Transactional upgrade applied to the isolated local stack; production migration history unchanged |
| October 7, 2026 | `uv run --locked pytest -q tests/integration/supabase/test_review_regressions.py tests/integration/supabase/test_worker.py --tb=short` | 27 passed in 19.98s, including additional claim boundaries, live worker execution and legacy receipt upgrade |
| October 7, 2026 | `uv run --locked pytest -q tests/contracts tests/test_systemone.py --tb=short` | 276 passed in 11.87s; one upstream TestClient deprecation warning |
| October 7, 2026 | `deno check supabase/functions/ollama/index.ts` and `deno test --allow-read=protocol supabase/functions/ollama/gateway_test.ts` | Type check passed; 42 gateway contract tests passed |
| October 7, 2026 | `uv run --locked pytest -q tests/integration/supabase/test_edge.py -k first_record --tb=short` | 1 failed, 3 deselected in 7.22s; known proxy disconnect still leaves SQL state `succeeded` |
| October 7, 2026 | `uv run --locked pytest -q tests/integration/supabase -k 'not local_ollama_model' --tb=short` | 45 passed, 1 failed, 1 deselected in 65.82s; only strict Edge disconnect failed; prior intermittent 503 did not recur |
| October 7, 2026 | `OSHEPHERD_REAL_OLLAMA_MODEL=llama3.2:3b uv run --locked pytest -q tests/integration/supabase/test_edge.py -k local_ollama_model --tb=short` | 1 passed, 3 deselected in 12.73s; patched SQL/Edge/worker lifecycle returned genuine inference from the existing local model |
| October 10, 2026 | `uv run --locked pytest -q tests/contracts/test_supabase_local.py --tb=short` | 6 passed; explicit JSON status, private output/state, startup/status failures and origin rejection covered |
| October 10, 2026 | `uv run --locked python -m scripts.supabase_local start` | Passed against real CLI 2.116.0; expected local origin validated and private connection state saved |
| October 10, 2026 | `uv run --locked pytest -q tests/contracts tests/test_systemone.py --tb=short` | 282 passed in 11.97s; one upstream TestClient deprecation warning |
| October 10, 2026 | Final `uv run --locked pytest -q tests/contracts tests/test_systemone.py --tb=short` | 294 passed in 11.64s; one upstream TestClient deprecation warning |
| October 10, 2026 | Final `uv run --locked pytest -q tests/integration/supabase -k 'not local_ollama_model' --tb=short` | 62 passed, 1 failed, 2 deselected in 77.96s; only strict Edge disconnect failed (`succeeded` after reader closes) |
| October 10, 2026 | Deno gateway contracts and entrypoint type check | 44 passed; type check passed |
| October 10, 2026 | Real Ollama Edge smoke, spike and versioned release schemas | 2 passed in 9.10s; both genuine `done: true`, six tokens; no prompt/output printed |
| October 10, 2026 | Versioned install/replay/upgrade/rotation/enrollment/disable recovery suite | 6 passed; lost responses/config writes preserve identities and keys; drift rejected |
| October 10, 2026 | Worker recovery/control suite | 9 passed; deterministic socket, refresh, fencing, inventory and ASGI stall cases |
| October 10, 2026 | Final capability-list validation and release integrity subset | 23 passed in 10.59s; 11 recovery cases plus 12 operator contracts; null/string capabilities retain control and recover |
| October 10, 2026 | Official CLI `supabase db push --local --dry-run` | Up to date; candidate migrations recognized without reapplication |
| October 10, 2026 | `uv build`, wheel asset inspection and empty-cache Vercel export/install/import | Passed; exact runtime pins retained; private state/tests/services excluded; unauthenticated health denied |
| October 10, 2026 | Hosted preparation after Vercel login | Official API confirms Hobby; isolated empty project created, inspected and linked. Supabase project inventory unchanged; no hosted function deployed |
| October 10, 2026 | Vercel upload policy and release contracts | 13 passed; explicit environment-variant exclusion added, FastAPI preset selected; provider defaults also exclude `.env.local` |
| October 11, 2026 | New isolated default-Free Supabase organization/project and TLS session-pooler connection | Created only the explicitly approved targets; PostgreSQL 17.11; existing project untouched; actual API exposure preserved |
| October 11, 2026 | Fresh hosted operator install/upgrade/doctor, Edge deploy, enrollment and actual CLI login | Passed; matching schema; server/worker keys separated; private journals/session retained |
| October 11, 2026 | Hosted Edge → actual CLI worker → local Ollama 0.40.2 | Passed; `llama3.2:3b`, genuine done, three generated tokens, 579 bytes in 4.72 seconds |
| October 11, 2026 | Hosted Edge deterministic route/stream/async/Storage probe | Route fixtures and exact NDJSON passed; replay reuses job ID; signed 1,200,347-byte download size/SHA-256 verified |
| October 11, 2026 | Strict hosted early-record/disconnect probe | Early nonterminal record in 0.84 seconds; **cancellation failed**, SQL `succeeded` after reader closed |
| October 11, 2026 | `uv run --locked pytest -q tests/contracts tests/test_systemone.py tests/integration/supabase/test_operator.py tests/integration/supabase/test_storage.py --tb=short` | 312 passed in 41.92 seconds; fresh/legacy history guards, masked error, grant preflight, trigger/RLS/permission drift and Storage commit covered |
| October 11, 2026 | Final same contract/operator/Storage selection with schedule-role reconciliation | 313 passed in 42.35 seconds; duplicate owned local timers removed, collisions rejected, unrelated jobs preserved |
| October 11, 2026 | Checked-in `scripts.supabase_hosted_check` against isolated Edge | Routes/NDJSON/private download pass again; first nonterminal record in 1.01 seconds; strict SQL cancellation assertion exits unsuccessfully with `succeeded` |
| October 11, 2026 | Hosted bounded Cron history and cross-cluster application-key probe | Twenty latest cleanup runs succeeded; other cluster's valid application key denied |
| October 11, 2026 | Final `uv build` and credential-free Vercel export | Verified wheel/bundle regenerated; separate SQL cluster and enrollment configured locally; no secret upload or Vercel deployment |
| October 11, 2026 | Approved Vercel Production configuration and actual deployment | Eight encrypted Production-only variables verified; deployment ready; API confirms FastAPI and configured 150-second invocation budget; no database password/session uploaded |
| October 11, 2026 | Hosted Vercel actual CLI worker and local Ollama | Genuine completion, two generated tokens, 576 bytes in 5.56 seconds |
| October 11, 2026 | Checked-in strict hosted probe against Vercel | Routes, exact NDJSON, metadata/auth and signed 4,200,347-byte download pass; first nonterminal record in 2.22 seconds; SQL cancellation **fails**, state `succeeded` |
| October 11, 2026 | Combined contracts/SystemOne/local integration after API-exposure repair | 370 passed, one failed, two opt-in model cases deselected in 109.58 seconds; only known strict local Edge disconnect fails |
| October 11, 2026 | Real hosted lifetime probes with concurrent enrolled workers | Edge/Vercel sync return 504 `execution_expired` at 135.23/135.63 seconds; async succeed with genuine fixture completion at 409.14/410.81 seconds |
| October 11, 2026 | Real two-session Storage commit-lock regression before fix | Production case fails: non-key metadata update succeeds while trigger transaction holds only key-share; spike case passes |
| October 11, 2026 | Additive `20261011000500` upgrade and final combined suite | Hosted upgrades succeed; both clusters have zero active jobs; 370 passed, one known strict Edge disconnect failed, two model cases deselected in 112.62 seconds; concurrency regression passes |
| October 11, 2026 | Final wheel/locked Vercel export and post-upgrade hosted probes | Seven immutable SQL artifacts verified; both hosted routes/NDJSON/auth/downloads pass again; early records at 0.55/1.81 seconds; both strict disconnect assertions fail with `succeeded` |
| October 11, 2026 | Hosted Edge event-only control/disconnect diagnostic | Completion hook unavailable; reader closes after first record; pulls continue through 8 seconds; abort follows stream completion; temporary function and two private objects removed |
| October 11, 2026 | Traced Vercel strict probe with private event reports | First record/reader close at 1.27 client seconds; final body and ASGI disconnect at 4.97 handler seconds; SQL `succeeded`; routes/NDJSON/download pass; three reports deleted and normal gateway restored |
| October 11, 2026 | Checked-in `--lifetimes` mode on both isolated profiles | Edge/Vercel sync expire at 135.25/135.91 seconds; async fixture completions succeed at 408.69/408.55 seconds; both mode exits successful |
| October 11, 2026 | Lifetime failure guards plus gateway/operator contracts | 39 passed in 0.45 seconds; foreign status URL receives no key, owned cancellation attempted, lost cancellation acknowledgement preserves failure and stops concurrent sync request |
| October 11, 2026 | Final service-free contracts/SystemOne suite for the probe slice | 303 passed in 11.85 seconds; runtime/package source unchanged; one upstream TestClient deprecation warning |
| October 11, 2026 | Initial bounded hosted lifecycle on both profiles | Capacity/cancel/silence/expiry pass; cleanup wait times out because 60 seconds is shorter than the configured five-minute intent quarantine; no due deletion or failed Storage claim observed |
| October 11, 2026 | Corrected checked-in `--bounded-lifecycle` on Edge and Vercel | Both exit successfully; four async jobs succeed with peak two and queued work; explicit cancellation stops worker; silence deadline/heartbeat pass; metadata takes 0.27/0.80 seconds; signed/content expiry retain success tombstone; each runner removes two expired objects after normal quarantine |
| October 11, 2026 | Final service-free suite including active-installation guard | 304 passed in 11.85 seconds; targeted probe/maintenance guards 10 passed; production runtime/migration source unchanged |

## October 12 independent implementation and qualification

Implemented the separate local OAuth control service, encrypted expiring records,
durable queued shared-engine setup, private portable worker exports, explicit
inference verification and ownership-bound Edge/Vercel deployment/removal adapters.
The [installer runbook](experiments/local-installer.md) records local commands and
the unrun hosted OAuth/button gates. The user chose local-first implementation
because no OAuth app/callback/service exists yet. Applied SQL and gateway/runtime
contracts are unchanged; both profiles remain experimental.

Evidence collected in this slice:

- Full contracts/SystemOne plus real local operator/Storage selection: **347
  passed in 51.87 seconds**, one upstream TestClient warning. The subsequent
  corrected Auth-probe subset has **9 passes**, including 401/403 rejection and
  failure on a still-valid 200 response. Final offline suite: **337 passed in
  13.67 seconds**. Broad deterministic/local selection: **402 passed**, with
  five Edge cases initially blocked by the hosted probe's port. Sequential Edge
  rerun: **four passed, one known strict disconnect failure, two model cases
  deselected in 22.12 seconds**. Together these cover 406 passing cases; this is
  combined selection evidence, not a claim of one clean combined invocation.
  Deno: **44 passed**, entrypoint type check passed.
- Both hosted profiles completed **1,000 exact deterministic jobs** at concurrency
  two. Edge load duration 453.49 seconds, p50/p95/max 0.640/1.291/4.443 seconds;
  Vercel 741.74 seconds, 1.235/1.943/4.035 seconds. Worker control and metadata
  stayed available through a one-hour observed refresh/persistence cycle.
- The first full soak exited unsuccessfully at the final old-token check because
  it accepted only 401; the original response status was not recorded. GoTrue's
  [JWT parser](https://github.com/supabase/auth/blob/master/internal/api/auth.go)
  uses 403 for invalid JWTs. The corrected harness accepts rejection via 401/403,
  still fails on 200, and records the status. A separate `--auth-soak` repeats
  actual expiry acceptance without repeating load. Both corrected modes now
  exit successfully: original JWT **403**, current JWT **200**, unchanged user,
  atomic private persistence, live control/metadata and exact inference after
  expiry. Edge/Vercel elapsed **3224.12/3225.09 seconds** from sessions initially
  having 3209/3208 seconds remaining. Metadata checks **107/104**. These repeat
  zero load jobs plus one final inference each; no JWT lifetime was shortened.
- Both `--recovery` probes pass: two actual worker restarts advance generation,
  old heartbeat/append/failure mutations are fenced, pre-output work retries once,
  durable/incomplete output never retries, and exact 536,405-byte slow-reader
  responses succeed in 8.89/10.16 seconds. Probe-process peak RSS was
  69,730,304/69,058,560 bytes; it includes the client/fake upstream, not hosted
  gateway memory.
- One/five-reader workload on Vercel returned exact NDJSON; maximum first-record
  time 2.462/15.482 seconds. Edge's repeated five-reader burst returns **429
  `capacity_exceeded`**. Both clusters retain the actual default caller budget
  **four active jobs**, total 128, queued/running 64, and an enrolled capacity-two
  worker. Five overlapping HTTP requests do not prove five simultaneously admitted
  jobs; no limit was raised to remove the rejection.
- Vercel adapter actual Hobby/account/verified-domain preflight, eight encrypted
  Production-only values, clean-wheel deployment, anonymous denial/authenticated
  health and repeat-deployment reconciliation pass. No database password/session
  or Preview secrets uploaded, and no hosted resource removed.
- A direct dirty-tree wheel retained old test files and was rejected **before
  upload**. Rebuilding from the clean source archive excluded tests/private state.
  The installed wheel's source/assets hashes and CLI doctor passed from a clean
  working directory without checkout imports.
- Aggregate observation on the owned database: **26,138,291 bytes**, zero active
  jobs, 1,067/1,063 retained cluster jobs and 13 private result objects per cluster.
  The observation includes earlier isolated tests and ordinary retention; it is
  not a claim about remaining provider quota. Auth workers stopped cleanly and
  the user's Ollama/local stack were preserved.

Remaining gates: existing strict disconnect failures, wider size/backpressure
and quota envelope, real hosted OAuth/Marketplace/button onboarding, actual Edge
Management-adapter deployment and reviewed disposable-resource removal. Short
functional/load checks do not qualify a profile. The current user decision
preserves the contract and experimental status.

## October 13 documentation reconciliation and next work

Reconciled the summary tables, [handoff](development-handoff.md),
[installation status](installation.md) and [ordered next steps](next-steps.md)
with the October 12 evidence. This documentation-only slice adds no runtime
changes or new hosted validation. Delivery steps 6–7 remain open for:

1. Broader hosted size, backpressure, latency and quota measurements within the
   existing Free/Hobby authorization.
2. Full fresh/repeated packaged CLI onboarding; installed-wheel doctor alone
   does not establish the entire lifecycle.
3. Actual Edge Management-adapter deploy/adoption/recovery and reviewed removal
   of explicitly selected disposable owned resources.
4. Real OAuth prerequisites, hosted consent/onboarding and published pinned
   template/button acceptance against V16–V21.
5. Final release review only when the required profile gates pass.

The strict disconnect failures remain an open release gate under the user's
contract-preservation decision. Independent acceptance work cannot qualify either
profile while that gate fails. Paid cases, API expansion and an alternative
cancellation protocol are outside the current work.

## October 14 independent acceptance and resource retirement

The [acceptance report](experiments/independent-acceptance.md) records the new
envelope, installed-wheel and reviewed-removal harnesses, exact hosted sizes and
remaining gates. Real acceptance found and fixed two defects: flattened Edge
upload paths prevented exact CLI artifact adoption; oversized upload cancellation
could stall rejection. Source-relative uploads preserve strict hash checks.
Request rejection now discards a finite tail without retaining it, preserves the
one-MiB accepted budget and returns 413 for a five-MiB Edge upload in 0.468 seconds.
Applied SQL and inference cancellation contracts are unchanged.

Verification:

- Final offline contracts/SystemOne: **363 passed in 14.07 seconds**, one upstream
  TestClient deprecation warning. Final combined deterministic/local selection:
  **429 passed, one known strict Edge disconnect failure, two model cases
  deselected in 122.79 seconds**; subsequent cleanup/retired-state guards are
  included in the offline count, not that earlier combined invocation.
- An earlier combined run had **422 passes and two failures in 129.96 seconds**:
  strict disconnect and initial route-health 503. The route passed in a focused
  rerun (**one pass in 5.93 seconds**) and the final full selection; its transient
  cause remains unestablished. No retry/skip was added to its assertion.
- Deno: **48 passed**, entrypoint type check passes. Current clean archive/wheel
  build and full installed-wheel local lifecycle pass, including real JSON/
  terminal NDJSON and generation advancement after CLI worker restart.
- Both envelope modes pass with exact JSON at 1,048,576/4,000,000 bytes and paused
  NDJSON reads of 819,224/3,178,592 bytes. Earlier Edge timeout reports are retained.
- Both final hosted strict probes pass routes/NDJSON/downloads and still fail
  reader disconnect cancellation, ending SQL `succeeded`.
- Actual Edge adoption, Management upgrade and repeated reconciliation pass. Both
  exact gateway removals were explicitly approved, then pass real deletion,
  labelled simulated acknowledgement-loss recovery and repeated removal.
  Retained job counts **1,059/1,031**, job-ID digests, schema fingerprint and Cron
  commands match before/after. Post-removal reads verify Edge secret digests and
  Vercel's eight encrypted Production-only variables remain; projects persist.

Both hosted test clusters are now disabled and their keys/workers revoked.
All owned test processes stop; the user's Ollama/local stack remain. The user
reconfirmed local-first OAuth work because no app/callback/host exists. Fresh
hosted/browser onboarding, wider performance/resource/quota evidence and public
pinned templates/buttons remain unrun; strict disconnect qualification remains
open and both manifest profiles stay `qualified: false`.

## October 14 adoption-first CLI implementation

- Committed the user's revised priority in `3ae5a70`: share/join user adoption
  ahead of hosted OAuth/buttons and further benchmark expansion. Three implementation
  agents delivered separate invitation/join, share/supervision and provider setup
  modules; the orchestrator integrated and cross-reviewed them.
- Added source `share`, `invite` and `join`, a guided existing Supabase Edge project
  path, private generated connection/owner state, portable restricted invitations,
  restart without provider Management access after completed setup, and supervised
  worker/Storage cleanup. See the [user guide](../../setup-supabase.md) and
  [acceptance report](experiments/cli-onboarding.md).
- Review fixed worker-subject binding, uncertain one-time exchange replay,
  overlapping signal ownership, early worker exits, actionable discovery errors,
  and an invitation output race. The original race test failed before the intended
  write because of invalid fixture credentials; corrected valid-key tests now prove
  exclusive publication and successful export.
- Final offline contracts/SystemOne: **454 passed in 14.44 seconds**, with the
  existing upstream Starlette TestClient deprecation warning. Focused real local
  operator/maintenance/worker regressions: **24 passed in 22.78 seconds**.
  Provider setup's HTTP/Auth coverage is mocked, not hosted onboarding evidence.
- Built the reviewed candidate sdist/wheel and ran
  `scripts/supabase_onboarding_check.py` from `/private/tmp` under isolated uv with
  that installed wheel. Repeated share preserved identity and advanced SQL worker
  generation; foreground SIGINT stopped worker/cleanup successfully; invite/join
  and join restart passed with separate state; joined files contained no admin,
  database or application key; genuine `llama3.2:3b` JSON and terminal NDJSON passed.
  This was a simulated second machine on the same host and an existing local
  Supabase stack/persistent gateway, not a new hosted project.
- Final private report: `.oshepherd/local/onboarding-reviewed-20261014.json`;
  wheel: `.oshepherd/local/onboarding-reviewed-build/`. Harness-owned processes
  stopped and its local cluster was disabled; user Ollama/local Supabase remained
  running. No cloud writes, model downloads, public release or push occurred.
- Remaining A1–A5 work: new project creation with durable reconciliation/free-plan
  constraints, guided Vercel setup, eliminating the private database URL/`psql`
  prerequisite, optional services, and published-package/fresh hosted acceptance.
  SQL/gateway contracts and experimental flags are unchanged. Strict disconnect
  failures remain open; this slice did not rerun or waive them.

## Handoff discipline

Update this file after every implementation slice with paths, exact verification
and remaining work. Keep failed and unrun gates visible. Never mark the full
objective complete based on mocked transport alone: the Edge runtime, SQL/Auth
authorization and external worker must participate in the verified lifecycle.
