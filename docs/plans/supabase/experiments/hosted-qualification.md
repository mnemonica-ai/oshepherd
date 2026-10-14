# Hosted candidate qualification

Updated October 14, 2026. Hosted Edge and Vercel inference passed, but both profiles
remain experimental because their strict reader-disconnect checks fail. This report
records actual provider behavior; local or mocked evidence does not close that
gate.

The October 14 [independent acceptance](independent-acceptance.md) records current
size/adapter/packaged CLI and reviewed live-removal evidence. Both hosted test
clusters are now disabled and their reviewed gateway resources removed. Provider
projects, retained data and shared configuration remain; the commands below
require newly selected disposable installations, not the retired keys/sessions.

## Isolated targets and setup

The explicitly approved new Supabase organization/project were created without
an upgrade, paid size or add-on. The project is in São Paulo and runs PostgreSQL
17.11. Its plan basis is Supabase's documented default Free plan for a new
organization; the CLI does not expose an independent billing-plan field. The
previous organization/project were left untouched. The selected Vercel team
was verified as Hobby through the official team API; its separate test project
is linked locally. Credentials, target IDs and operator journals stay in owned,
ignored private files.

The Supabase CLI is 2.116.0. The hosted database uses a session pooler over TLS;
Auth/RPC calls use modern publishable/server keys. Read-only namespace/API
probes confirmed existing exposure, and installation preserved `public` as the
default and retained `graphql_public`. An explicit synthetic test owner was
bound before application-key creation. Actual operator enrollment and
`supabase-worker-login` saved a protected worker session.

Supabase documents the [new organization Free-plan default](https://supabase.com/docs/guides/troubleshooting/keeping-free-projects-after-pro-upgrade-Kf9Xm2)
and recommends treating the [Storage schema as read-only](https://supabase.com/docs/guides/storage/schema/design).

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
| Generate/chat/embed/SystemOne fixtures and metadata routes | Passed through real hosted Edge, SQL/Auth and loopback fake upstream |
| Streaming byte compatibility | Generate and chat match the committed NDJSON fixtures exactly |
| Async idempotency and private Storage download | Passed; same job ID on replay; 1,200,347 bytes, signed download size and SHA-256 verified |
| Hosted early-record delivery | First nonterminal record received in 0.84 seconds; checked-in probe repeated at 1.01 seconds, before completion |
| Strict HTTP reader disconnect → SQL cancellation | **Failed**; SQL state became `succeeded`, not `cancelled` |
| Actual hosted cleanup and cluster isolation | Twenty latest Cron runs succeeded; a valid key from the separate test cluster was denied |
| Service-free contracts plus local operator/Storage regression selection | 313 passed in 42.35 seconds; one upstream TestClient deprecation warning |
| Vercel Production deployment | Eight encrypted Production-only variables explicitly approved/uploaded; deployment ready; official deployment API resolves FastAPI and a configured 150-second invocation budget |
| Vercel real enrolled worker and Ollama | Passed; genuine completion, two generated tokens, 576 bytes, 5.56 seconds |
| Vercel routes, exact NDJSON, metadata and application auth | Passed through actual hosted HTTP/SQL/Auth; unauthenticated health returns 401 |
| Vercel async replay and oversized private download | Passed; same job ID on replay; 4,200,347 bytes downloaded with size/SHA-256 verified; direct inline retrieval returns 413 |
| Vercel early-record/disconnect | First nonterminal record in 2.22 seconds; **SQL cancellation failed**, state `succeeded` after reader closed |
| Hosted synchronous deadline | Edge returns 504 `execution_expired` in 135.23 seconds; Vercel in 135.63 seconds. This measures the application/SQL limit, not the provider's full invocation ceiling |
| Async lifetime beyond a gateway invocation | Edge and Vercel jobs succeed at 409.14 and 410.81 seconds respectively; fixture completion retrieved through actual hosted status/result routes |
| Final combined contracts and local integration suite after lock fix | 370 passed, one failed, two opt-in real-model cases deselected in 112.62 seconds; only the known strict local Edge disconnect failed |
| Both hosted probes repeated after final lock upgrade | Routes, exact NDJSON, auth and private downloads pass again; first nonterminal records at 0.55/1.81 seconds; both strict cancellation checks still fail with `succeeded` |
| Extended soak, hosted saturation/backpressure, clean-machine OAuth installer | Not run |

The test worker and loopback upstream are stopped after each probe. The cloud
test project, private journals and generated test data remain available for
review. Neither a configured stage nor successful inference marks the full
hosting profile qualified.

The subsequent [disconnect signal investigation](disconnect-signals.md) measured
late provider signals: Edge aborts after stream completion, and Vercel Python
receives `http.disconnect` after the final body. The normal gateways were restored
and temporary diagnostic function/report objects removed. The strict assertions
remain failing; no product contract or hosting default changed.

## Reproduce the strict hosted probe

Use a dedicated isolated installation, enroll a worker with capacity two, and
exchange its one-use challenge as described in the [operator runbook](operator-lifecycle.md).
Stop other workers for that installation: this probe temporarily publishes fake
model inventory and creates bounded test jobs, including a 1.2 MB Edge result or
a 4.2 MB Vercel result. The
checkout development environment supplies the shared deterministic fixtures.

```sh
uv sync --locked --extra supabase
uv run --locked python -m scripts.supabase_hosted_check \
  --connection-file <private-operator-connection.json> \
  --state-dir <private-operator-state-directory> \
  --worker-session <private-enrolled-worker-session.json> \
  --report-file <private-report.json>
```

The probe binds its fake upstream to loopback port 55324 and uses the real
provider gateway. It emits booleans, sizes and timings, never prompt/output or
credentials. Its final disconnect assertion exits unsuccessfully when SQL does
not cancel; the failure must not be skipped, weakened or replaced with FastAPI
evidence. For real-model inference, run the normal enrolled CLI worker and use
`supabase verify-inference --model llama3.2:3b` with the same private connection
and state directory.

## Reproduce hosted lifetime checks

Use the same isolated installation/session with capacity two and stop other
workers first. This separate mode runs a 405-second async upstream alongside a
145-second synchronous upstream, checks the 135-second application deadline,
and verifies the retained async completion. It waits for the async upstream
request to start before changing scenarios. The async poll budget is 450 seconds
with ten-second polling; expect roughly seven minutes overall. It does not run or
waive the strict reader-disconnect test.

```sh
uv run --locked python -m scripts.supabase_hosted_check --lifetimes \
  --connection-file <private-operator-connection.json> \
  --state-dir <private-operator-state-directory> \
  --worker-session <private-enrolled-worker-session.json> \
  --report-file <private-lifetime-report.json>
```

Use `--upstream-port <port>` for a distinct loopback port when testing separate
clusters concurrently. Do not start two workers for the same enrollment/session.
The checked-in mode passed on both providers: Edge/Vercel synchronous expiry at
135.25/135.91 seconds and async success at 408.69/408.55 seconds. Reports contain
safe booleans, states and timings. A failed check attempts explicit cancellation
of its accepted async job and stops its concurrent synchronous request, preserving
the original failure if a cancellation acknowledgement is lost. It rejects a
foreign status URL before sending credentials and cancels via the known owned URL.

## Reproduce bounded lifecycle checks

`--bounded-lifecycle` tests four async jobs through an enrolled capacity-two
worker, queued/running observations, explicit cancellation, a silent upstream
under a ten-second total deadline, live metadata/heartbeat control, signed URL
expiry, retained success tombstones after content expiry, and actual expired
Storage-object removal by the cluster-scoped maintenance runner.

```sh
uv run --locked python -m scripts.supabase_hosted_check --bounded-lifecycle \
  --connection-file <private-operator-connection.json> \
  --state-dir <private-operator-state-directory> \
  --worker-session <private-enrolled-worker-session.json> \
  --report-file <private-lifecycle-report.json>
```

Use an idle, dedicated isolated test installation with no other worker processes;
the mode checks for active jobs before starting its fake worker. It creates six
synthetic jobs and shortens content retention only for one newly generated
successful job. Maintenance applies the cluster's existing expiry policy, so it
can also remove previously expired test data in that same installation. It does
not delete other clusters or modify the production schema/configuration.

Fresh deletion intents have a five-minute quarantine. The first run incorrectly
used a 60-second cleanup wait and timed out with no due deletion claim; outbox
inspection confirmed this was the harness budget, not a failed Storage request.
The corrected mode honors that interval, polling maintenance every ten seconds
within a 390-second cleanup budget. Expect roughly six minutes overall. The
fake server has bounded graceful shutdown for pending silent requests.

The hosted burst/cancel/deadline/expiry cases passed on both providers: four
successes, peak two worker jobs, queued work observed, explicit cancellation
stopping the worker, deadline expiry during silence, fresh heartbeat and live
metadata. These are bounded functional checks, not sustained load/soak or proof
of HTTP reader-disconnect cancellation. The strict default mode remains separate.

Both corrected mode runs exited successfully. Metadata during upstream silence
returned in 0.27 seconds on Edge and 0.80 seconds on Vercel; heartbeats remained
fresh. The signed URL expired before physical removal, ordinary result retrieval
returned 410, and status retained `succeeded` with unavailable content. Each
maintenance runner removed two expired objects, including the prior run's pending
intent and the new test object, after their configured quarantine. No quarantine
setting or production migration was changed. The updated service-free suite has
304 passing tests, including refusal to start a fake worker on an active
installation.

## October 12 load, recovery and installer evidence

Both profiles completed 1,000 exact deterministic short jobs at concurrency two:

| Profile | Load duration | p50 | p95 | Maximum |
| --- | --- | --- | --- | --- |
| Edge Free | 453.49 s | 0.640 s | 1.291 s | 4.443 s |
| Vercel Hobby | 741.74 s | 1.235 s | 1.943 s | 4.035 s |

The first one-hour soak observed automatic worker refresh and private persistence,
but exited unsuccessfully at a final check that expected only 401. Its original
response status was not retained. GoTrue uses 403 for invalid JWTs; the corrected
probe records the status and requires 401/403 rejection, current-token acceptance
and exact inference after the original expiry. `--auth-soak` repeats that actual
cycle without repeating load. Both corrected runs now pass: original JWT 403,
refreshed JWT 200 for the same user, saved session equality and exact inference
after expiry. They ran 3224.12/3225.09 seconds with 3209/3208 seconds initially
remaining, checking metadata 107/104 times. Each repeats zero load jobs and one
post-expiry inference. No server expiry configuration or local JWT schedule was
shortened. The initial failed reports remain separate.

Both bounded recovery probes passed actual worker restarts before/after output,
old-generation heartbeat/append/failure fencing, exactly one pre-output retry,
no retry after durable output or incomplete EOF, and exact 536,405-byte slow
HTTP reads. Slow responses completed in 8.89/10.16 seconds. Process peak RSS was
69,730,304/69,058,560 bytes for the whole local probe/client/upstream process;
hosted gateway memory was not measured.

The default per-caller active-job limit is four on both clusters, total active
128, queued/running 64, and worker capacity two. Vercel returned all five exact
streams in a concurrent HTTP batch as earlier admissions finished; Edge's repeat
returned 429 `capacity_exceeded`. This does not establish five simultaneous jobs
for one caller. Limits were preserved. The first Edge failed batch did not record
its status; the instrumented repeat records 429 and its stable code.

Reproduce only on idle, dedicated installations with the enrolled test worker:

```sh
uv run --locked python -m scripts.supabase_hosted_check --soak \
  --connection-file <private-connection.json> --state-dir <owned-operator-dir> \
  --worker-session <private-worker-session.json> --report-file <private-report.json>
# --auth-soak measures only actual expiry/refresh; --recovery is a separate mode.
```

These modes refuse active jobs before replacing the test worker and write 0600
reports. Use separate loopback ports for concurrent profiles. Do not run local
Edge integration cases while a hosted probe owns port 55324; run them sequentially
or select another probe port before starting its expiry cycle. The one/five-reader
script uses an already running isolated fixture worker and does not register or
restart it:

```sh
uv run --locked python -m scripts.supabase_hosted_stream_load \
  --connection-file <private-connection.json> --state-dir <owned-operator-dir> \
  --report-file <private-stream-report.json>
```

The [local installer/adapters](local-installer.md) have ASGI/HTTP/CLI fault tests.
Vercel's actual adapter verifies Hobby/project/domain ownership, encrypted
Production-only configuration, a clean-wheel deployment, anonymous denial,
authenticated health and a repeated call reusing the deployment. No database
password/session or Preview secret was uploaded; no hosted gateway was deleted.
At the end of October 12, Edge Management/OAuth/button/removal acceptance was
unrun. October 14 closes the tested Edge adapter and live removal subset; real
OAuth/button onboarding remains unrun.

All evidence is a bounded subset. October 14 adds tested size boundaries and
paused readers. Sustained server-side backpressure, gateway memory and
remaining provider quota are not established. Both profiles remain unqualified;
none of these measurements closes reader-disconnect cancellation.

The [Vercel cancellation API](https://vercel.com/docs/functions/functions-api-reference#cancel-requests)
currently documents its `supportsCancellation` opt-in for Node.js only. It does
not establish Python ASGI disconnect delivery. The production template therefore
does not claim this setting fixes the measured Python failure. Local ASGI
cancellation passing is separate evidence. Both provider gates require a
supported transport/deployment decision and another strict hosted measurement;
the assertion and `qualified: false` remain intact.
