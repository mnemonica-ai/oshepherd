# Supabase implementation progress

Updated October 11, 2026. The objective is to finish the implementation so
an Ollama client can send a request to an Edge Function and receive inference
from a local Ollama model. This ledger records authoritative artifacts and
verification; an implementation checkbox does not imply hosted validation.

The local testing milestone is achieved: a real Edge Function request reaches
the external worker and returns genuine Ollama inference. The complete handoff
is still unfinished; production qualification, hosted/load verification and the
hosted proxy disconnect gates remain open below. New Python work uses uv by default;
older verification-log commands retain their original invocation.

Continue with the [ordered next steps](next-steps.md): hosted cancellation first,
then complete load/recovery evidence, clean-machine onboarding, provider adapters,
OAuth/button flows and release qualification. That checklist defines acceptance
criteria and separates external installer setup from work possible now.

## Completed

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
  [worker recovery evidence](experiments/worker-recovery.md). Soak and hosted
  downstream backpressure remain unverified.
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
| Worker enrollment/session storage and refresh | Auth/storage/membership/login and operator enrollment implemented | Local recovery/refresh passed; actual hosted enrollment and CLI login passed; hosted refresh/long soak pending |
| External local Ollama worker and independent heartbeat | Implemented with separate bounded metadata pool | Fake/real model, deterministic restart/fencing, saturation and refresh passed; drain/extended soak pending |
| All nine Edge routes and application-key authentication | Implemented | Local and hosted route fixtures/auth/metadata passed; full adversarial matrix pending |
| Durable streaming, cancellation and output limits | Implemented in SQL/worker/gateway | Exact bytes, local FastAPI cancellation and controlled ASGI stall passed; **local Edge and both hosted reader-disconnect checks fail**; hosted slow-reader/load pending |
| Async jobs, expiry, cancellation and authorized results/downloads | Inline/private-object paths implemented | Edge/FastAPI async and downloads passed; full expiry/adversarial matrix pending |
| Private Storage results and cleanup/reconciliation | Upload/sign/read, deletion outbox, orphan reconciliation, operator runner and private Cron sweeps implemented | Local authorization, recovery, purge, Cron and operator schedule reconciliation passed; hosted/load pending |
| Optional FastAPI backend and Vercel template | Backend-aware API, Vercel ASGI factory and wheel-backed candidate template deployed on Hobby | Hosted real model/routes/download and 135-second application limit passed; hosted disconnect fails; platform ceiling and load pending |
| CLI bootstrap/doctor and operator administration | Protected candidate configure/install/enroll/doctor/upgrade/rotate/revoke/disable implemented | Local lifecycle/recovery and manual hosted install/deploy/enrollment passed; clean-machine onboarding, automated deployment and physical uninstall pending |
| Installation manifest, OAuth installer and provider adapters | Hash-verified packaged manifest and explicit schema-exposure adapter implemented | Local resumability/ownership/upgrade passed; OAuth and provider automation pending |
| Runbook and automated Edge → worker → response harness | Local and [operator runbooks](experiments/operator-lifecycle.md), disposable two-schema harness available | Real inference passed in both schemas; complete hosted clean-machine setup pending |
| Hosted limits, load/cost, lifecycle and installation | Isolated Supabase project deployed on default Free plan; Vercel Hobby verified and approved Production configuration deployed | Both real inference/routes/download pass; sync deadlines and async jobs beyond 400 seconds measured; both disconnect checks fail; broader measurements pending |

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

## Handoff discipline

Update this file after every implementation slice with paths, exact verification
and remaining work. Keep failed and unrun gates visible. Never mark the full
objective complete based on mocked transport alone: the Edge runtime, SQL/Auth
authorization and external worker must participate in the verified lifecycle.
