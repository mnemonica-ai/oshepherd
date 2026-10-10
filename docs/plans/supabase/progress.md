# Supabase implementation progress

Updated October 10, 2026. The objective is to finish the implementation so
an Ollama client can send a request to an Edge Function and receive inference
from a local Ollama model. This ledger records authoritative artifacts and
verification; an implementation checkbox does not imply hosted validation.

The local testing milestone is achieved: a real Edge Function request reaches
the external worker and returns genuine Ollama inference. The complete handoff
is still unfinished; production installation, hosted/load verification and the
Edge proxy disconnect gate remain open below. New Python work uses uv by default;
older verification-log commands retain their original invocation.

## Completed

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
  verified upload recovery and authoritative missing-object rejection through a
  Storage foreign key. The runtime owner has no Storage table read grant.
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
| Database schema, RPC grants/RLS, capacity, leases and fencing | Experimental spike implemented | Identity, quota, claim race, replay/fencing passed; full adversarial matrix and production migrations pending |
| Worker enrollment/session storage and refresh | Auth/storage/membership/login implemented | Auth exchange and SQL revocation passed; full operator administration pending |
| External local Ollama worker and independent heartbeat | Implemented | Fake upstream and real model passed; process restart/drain/refresh soak pending |
| All nine Edge routes and application-key authentication | Implemented | Local routes passed; full error/metadata matrix and hosted runs pending |
| Durable streaming, cancellation and output limits | Implemented in SQL/worker/gateway | Exact bytes/incomplete EOF passed; **local proxy disconnect gate fails**; slow-reader/load pending |
| Async jobs, expiry, cancellation and authorized results/downloads | Inline/private-object paths implemented | Edge/FastAPI async and downloads passed; full expiry/adversarial matrix pending |
| Private Storage results and cleanup/reconciliation | Upload/sign/read, durable deletion outbox, orphan reconciliation, operator runner and private Cron sweeps implemented | Local authorization, recovery, bounded purge and actual Cron passed; hosted/load/installer schedule reconciliation pending |
| Optional FastAPI backend and Vercel template | Supabase FastAPI, backend-aware factory and Vercel ASGI composition implemented | Redis characterization and real Supabase routes/disconnect passed; release template and deployed limits pending |
| CLI bootstrap/doctor and operator administration | Worker backend/login, maintenance and persistent local setup/doctor implemented | Repeated local setup and real lifecycle passed; general operator/bootstrap administration pending |
| Installation manifest, OAuth installer and provider adapters | Pending | Resumability/ownership/upgrade; hosted integration prerequisites |
| Runbook and automated Edge → worker → response harness | Disposable harness plus persistent setup/serve/doctor/request and [local runbook](experiments/local-edge-lifecycle.md) available | Production installation runbook and clean-machine setup pending |
| Hosted limits, load/cost, lifecycle and installation | Not run | Authorized isolated Supabase/Vercel targets, plan/region and budget |

## Current decisions and remaining dependencies

Redis remains the default. Supabase defaults to Edge and keeps async and optional
FastAPI in scope. There is no automatic cross-backend retry. Production migrations
must follow a reviewed, versioned protocol; temporary spike SQL stays separate.

Worker enrollment will be operator-provisioned Supabase Auth identities with
membership/capacity bound in SQL. The proposed browserless exchange uses an
administrator-generated, single-use Auth token followed by the worker's direct
Auth verification. This passed against local Auth with public signup disabled.
No worker receives a project administrator credential.

No cloud spending or deployment to an unspecified account is authorized. Hosted
verification requires selected isolated projects and a test budget. Local work
continues independently. Real model/GPU access will be checked without reading
secret environment files or downloading models implicitly.

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
in place; no hosted deployment has been run.

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

## Handoff discipline

Update this file after every implementation slice with paths, exact verification
and remaining work. Keep failed and unrun gates visible. Never mark the full
objective complete based on mocked transport alone: the Edge runtime, SQL/Auth
authorization and external worker must participate in the verified lifecycle.
