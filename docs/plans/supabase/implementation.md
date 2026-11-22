# Supabase broker: implementation plan

Updated November 22, 2026. Status: implemented experimental candidate with local
and bounded hosted evidence in the [progress ledger](progress.md). The strict
Edge reader-disconnect gate still fails; full release qualification and
hosted OAuth/button onboarding remain incomplete. The user-approved test gateway
removals are complete, so those hosted installations are retired. The
[compatibility matrix](compatibility.md) and [installation plan](installation.md)
remain the acceptance specification. Start at the [Supabase index](index.md).

## 1. Decision

**Keep the existing FastAPI + Celery + Redis deployment supported and unchanged by default. For the new Supabase backend, make Edge Functions the default gateway, accepting hosted limits. Keep FastAPI + Supabase available for a persistent server within the implemented limits.**

This follows the chosen product priority: a Supabase deployment should work without operating an API server. Preserve ordinary Ollama clients, streaming, every existing route including `/v1/systemone`, workers behind NAT, concurrent workers, retries, and cluster metadata within a documented Edge envelope. Durable asynchronous jobs are available from the default Edge gateway. Longer persistent FastAPI budgets remain a future qualification target; the current candidate enforces 135 seconds. The hosted limits are an accepted compatibility difference, not a reason to defer Edge Functions.

Backend and HTTP gateway are separate choices:

| Deployment | Queue/results/state | HTTP entry point | Purpose |
| --- | --- | --- | --- |
| Legacy Redis | Celery + Redis | Existing FastAPI | Continue supporting all existing installations |
| Primary Supabase mode | Supabase Postgres; Auth for workers; Storage for large results | Supabase Edge Function | No operated API server; bounded synchronous requests and durable async jobs |
| Optional persistent gateway | Same Supabase backend | FastAPI on a container/VM/server | Persistent Python gateway; current SQL budget still applies |
| Async extension | Same Supabase backend | FastAPI or Edge Function | Durable jobs whose clients do not keep a request open |

Edge Functions are the default Supabase entry point, but the database/worker protocol does not depend on them. Externally running workers and the optional FastAPI gateway are not subject to the Edge runtime clock. They remain subject to their own configured deadlines, reverse proxies, clients, and resource limits. Document gateway selection as `edge` (default Supabase deployment) or `fastapi`; this is separate from preserving `redis` as the existing CLI backend default. The installer records `edge_free`; the Python gateway uses `persistent`. Neither implies an unlimited request lifetime. The installation plan separates the delivered CLI from future browser onboarding.

Keep Redis and Supabase queues independent; never silently resubmit a failed request to another backend. A worker process serves one backend/cluster. Both Supabase gateways may use the same queue, credentials, and worker pool because they share one job protocol. Do not allocate the same GPU to independent worker pools without a shared capacity policy.

The target is Oshepherd's actual Celery usage, not a general Celery replacement. Chords, task canvases, arbitrary Python tasks, Flower, Celery remote control, and Celery's process-pool administration are outside scope. Configuration with different semantics must be documented or rejected, never silently ignored.

## 2. Evidence and compatibility policy

The repository currently exposes nine routes, including `/health`. The complete behavior inventory, source links, known gaps, and acceptance cases are in the [compatibility matrix](compatibility.md). The focused System One suite passed locally: **13 tests**. This establishes a narrow existing baseline, not Supabase correctness.

Implementation policy:

1. First preserve existing successful requests, response shapes, defaults, stream records, and tested error behavior on Edge and both FastAPI backends, within each gateway's explicit limits. Redis remains the existing CLI default and existing environment files continue working.
2. Fix dangerous backend behaviors—lost output, blocking the event loop, stale attempt completion—without making them compatibility requirements. Record observable improvements.
3. Treat broader Ollama support as coordinated API work after backend parity. `/api/embed`, tools, chat images, structured JSON schemas, and upstream default changes must not accidentally differ by backend.
4. Deliver all existing routes on the default Edge gateway, including System One, within published time/size limits. Use the same fixtures for the optional FastAPI gateway. No automatic redirect, replay, or gateway switch after a request starts.

Examples that must not be overlooked: omitted `stream` currently means false; `/api/embeddings` expects `input` and the worker maps it to Ollama's legacy `prompt`; `/api/show` wraps metadata in `model_info`; `/v1/systemone` preserves extra fields, never streams, and maps worker errors to HTTP 500. Preserve these during backend extraction; improve them in separately documented API changes.

## 3. Architecture and alternatives

```text
Default Supabase deployment:
  Ollama client -> Edge Function -> authorized RPCs / durable output
                                          |
                          Supabase Postgres + optional private Storage
                                          ^
                                          | HTTPS RPC
                                  Supabase worker -> local Ollama

Optional Supabase entry point:
  Ollama client -> shared FastAPI routes -> SupabaseBackend -> same RPCs

Existing deployment:
  Ollama client -> shared FastAPI routes -> CeleryRedisBackend
                            -> Redis / Celery -> Celery worker -> local Ollama

Realtime wakes gateways/workers; durable cursors recover any missed notification.
```

### Queue choice

Use a **logged jobs table with short transactional RPCs and `FOR UPDATE SKIP LOCKED`**, not a Celery broker URL pointed at Postgres. Only the Supabase worker consumes this protocol. Celery's SQL result backend does not supply this project's queue, Pub/Sub, or metadata behavior. [Celery brokers and backends](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html)

Decision rationale:

| Option | Benefit | Cost / decision |
| --- | --- | --- |
| Jobs table + transactional claim | Atomic model/capability selection, capacity, lease, and job state in one place | Own retry/lease state machine; selected because heterogeneous model routing is central |
| Supabase Queues / PGMQ | Built-in visibility, archival, queue tooling | Still needs job state, fencing, worker authorization, cancellation, and model routing; consider for homogeneous model pools if measured queue maintenance warrants it |
| New Kombu/Celery transport | Could preserve Celery internals | Much larger maintenance surface; not warranted for one inference task |

PGMQ's visibility window does not establish exactly-once inference after worker failure. Avoid claiming that extension use alone solves execution duplication. Model-name sanitization is not a reason to reject it: queue IDs could be mapped safely. [PGMQ semantics](https://supabase.com/docs/guides/queues/pgmq)

### Stream transport choice

**Persist bounded batches of stream bytes; treat Realtime as optional notification.** The durable cursor is authoritative, so late subscription or a missing notification cannot lose output. This removes the subscribe-before-queue race and avoids making Realtime payload size a response limit.

| Option | Tradeoff | Decision |
| --- | --- | --- |
| REST Broadcast of output only | Low write overhead; gaps/disconnects can lose output | Do not use as the sole output source |
| Durable chunks + batched reads | More database I/O; replay, ordering, and completion are explicit | Initial implementation |
| Direct outbound worker WebSocket to FastAPI | Potentially lowest relay latency and database traffic | Adds gateway routing, reconnect/resume, and a second transport; optimize only if measurements justify it |
| One Postgres LISTEN connection per API process | Efficient wakeups for many requests | Optional optimization; session/direct connection, not one connection per stream |

Supabase explicitly does not guarantee every Realtime message arrives. Broadcast acknowledgement confirms server receipt; database-originated replay is not a durable replay mechanism for arbitrary REST broadcasts. [Realtime delivery](https://github.com/supabase/realtime/blob/main/README.md), [Broadcast](https://supabase.com/docs/guides/realtime/broadcast)

Start with HTTPS RPCs everywhere. Each Edge request uses its own authorized cursor reader and bounded buffer; do not depend on cross-invocation memory or a surviving isolate. Use private Realtime hints for job availability, first output and terminal state, with adaptive polling for output and fallback. Coalesce repetitive hints. Notifications initially contain opaque IDs/cursors, never prompts or tokens. Persistent FastAPI can multiplex reads through a shared collector per API process. Process reuse is an optimization, never required for progress or cleanup. A missed hint only increases latency until the next poll.

Measure an optional inline fast path during the Edge spike: broadcast already-durable batches to subscribed gateways so the healthy path can avoid one read RPC per batch, while gaps fall back to chunk reads. Do not commit to this optimization until its database/Realtime write amplification, authorization, message quota, and retention costs beat cursor reads. Database-originated Broadcast can retain its own copy of output beyond our chunk TTL, so enabling inline output requires an explicit retention policy; default hints avoid that copy.

If LISTEN becomes worthwhile, use a persistent session/direct connection and an autocommit listener. Subscribe, commit, then catch up from durable state; reconnect and catch up again. Notification payloads contain references only. PostgreSQL describes this startup race and delivers notifications around transaction completion. [LISTEN](https://www.postgresql.org/docs/current/sql-listen.html), [NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html), [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres)

## 4. Gateway and execution boundaries

### Current code layout

The implementation is split between the backend package, versioned release
assets and development harnesses:

```text
docs/README.md                         # user and developer documentation index
docs/plans/supabase/                   # designs, acceptance criteria and evidence
oshepherd/backends/supabase/           # operator, gateways, workers and CLI
  release/migrations/                 # immutable packaged migration histories
  release/functions/ollama/            # packaged Edge artifact
  release/manifest.json               # source hashes and selected histories
oshepherd/worker/executor.py           # shared Ollama execution
supabase/functions/ollama/             # Edge source and Deno tests
supabase/migrations/                   # versioned development SQL
protocol/                             # schemas and cross-language fixtures
tests/contracts/                      # offline compatibility and lifecycle tests
tests/integration/supabase/            # SQL/Auth/Edge integration tests
scripts/                              # candidate build and acceptance runners
```

The original proposed `backends/base.py`, `backends/redis.py` and
`supabase/install/` layout was not adopted. Existing Redis code stays in its API
and worker packages. Refresh packaged assets after source changes with
`python -m scripts.build_supabase_release`; never edit an applied migration.
The interfaces below describe design boundaries, not literal exported symbols.

### Interfaces and configuration

For Edge, implement a thin Deno HTTP adapter over versioned RPCs. Export request schemas/defaults and response/error fixtures from the current Python models into a checked-in protocol package; test TypeScript validation, unknown-field handling and defaults against those fixtures. Share the protocol specification, not a claim that Python code executes in Deno. SQL owns authorization and job transitions; the gateway owns HTTP and its runtime deadline. Validate POST bodies incrementally against byte limits before allocating large JSON objects.

For the optional FastAPI gateway, introduce a small backend contract used by routes; avoid spreading Supabase branches throughout each endpoint:

```python
# Conceptual interface; immutable IDs/data, not Celery AsyncResult objects.
submit(request, caller, limits, idempotency_key=None) -> JobHandle
wait_result(handle, cancellation) -> ResultEnvelope
iter_output(handle, cursor=0, cancellation=None) -> AsyncIterator[bytes]
cancel(handle, reason) -> None
metadata(operation, model=None) -> dict
readiness() -> BackendHealth
close() -> None
```

A result envelope contains body/result reference, error kind and upstream status, attempt, and timing information. The HTTP layer maps it to existing endpoint conventions. Stream envelopes remain internal; clients receive their existing NDJSON, not the transport wrapper. Registry/capability methods belong to the worker-facing adapter, not the public HTTP API.

- `CeleryRedisBackend` wraps current queue/result/PubSub/metadata behavior. Keep blocking Redis/Celery calls off the event loop. System One's Celery submit and result wait must remain in the **same worker thread**, as the existing test requires; do not move `AsyncResult` between arbitrary thread-pool calls.
- `SupabaseBackend` uses a reusable asynchronous HTTP client and a reader strategy selected by hosting profile: one collector per persistent API process. Page reads by bytes and per-job limits so a large stream cannot starve another. Metadata/status use the same connection pool.
- `OllamaExecutor` owns local inference and serialization, separate from Celery decorators, queue leases, and network publishing. Keep Celery's task name and envelope stable. It exposes result/stream/cancel operations usable by both worker loops.
- Select configuration before importing/constructing backend resources. Importing the CLI or starting Supabase must not initialize Celery, Redis, or `WorkerData` with Redis credentials. Construct clients in application lifespan and close them on shutdown.
- Use an explicit Ollama client with resolved host and connect/read policy. Precedence: explicitly supplied `OLLAMA_BASE_URL`, then `OLLAMA_HOST`, then localhost. Preserve legacy behavior in the Redis adapter until any host-resolution change is separately tested. Long model loading must not hit an incidental five-second HTTP read default; control deadlines/cancellation independently.

Add backend selection to **both** commands:

```sh
# Existing commands remain valid and select Redis.
oshepherd start-api --env-file .api.env
oshepherd start-worker --env-file .worker.env

# Optional FastAPI + Supabase: same API routes and client host.
oshepherd start-api --backend supabase --env-file .api.supabase.env
oshepherd start-worker --backend supabase --env-file .worker.supabase.env
```

The default Supabase setup deploys `supabase/functions/ollama`, points clients at `https://<ref>.supabase.co/functions/v1/ollama`, sets an application key, and starts only a Supabase worker. Running `start-api` is optional.

Proposed configuration ownership:

| Component | Required settings | Credentials it must never need |
| --- | --- | --- |
| Edge gateway | Project URL/server secret, cluster/admission settings, plan-specific application deadline and size limits | Redis credentials, worker refresh tokens |
| Supabase worker | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, enrolled installation/session file, Ollama host, concurrency | Project secret/service-role key, database password |
| Optional FastAPI | `OSHEPHERD_BACKEND=supabase`, project URL/server secret, explicit auth mode, request/size limits | Worker refresh tokens, Redis credentials |
| Application client | Gateway host and `OLLAMA_API_KEY` (explicit Authorization header for JS as needed) | Supabase administrative or worker credentials |

Provide checked-in `.env.example` files per component, a deploy script/config for the Edge function, and proposed `worker-login`, `enroll-worker`, `create-api-key`, and `revoke-worker` commands. Login runs as the worker identity; enrollment/key administration runs only as an operator. Implement these provisioning flows as part of the backend release, rather than leave manual SQL as the only setup path. No real secrets belong in the examples.

For the existing Python CLI, precedence is explicit CLI flag, then `OSHEPHERD_BACKEND`, then `redis`. Backend-specific Pydantic models validate only their own fields. Fix the environment loader to enumerate `model_fields`, including inherited fields, if introducing shared base models; it currently reads only `__annotations__`.

Keep `CONCURRENCY` and basic logging/server settings. Supabase claims only as many jobs as free execution slots: no speculative prefetch initially. `PREFETCH_MULTIPLIER` values other than 1 must produce an explicit unsupported-setting error in Supabase mode. Redis socket/broker settings remain Redis-only. Celery's prefork isolation and memory recycling have no automatic async-worker equivalent: use bounded buffers, per-job exception isolation, graceful drain, and an external process supervisor; document that difference.

## 5. Data model and atomic operations

Keep storage tables in a private `oshepherd` schema. Expose a minimal RPC-only `oshepherd_api` schema through the Data API; grant schema usage and specific function execution, not blanket table access. Enable RLS as defense in depth. Schema exposure requires explicit project configuration. [Custom schemas](https://supabase.com/docs/guides/api/using-custom-schemas)

| Table | Important fields and constraints |
| --- | --- |
| `clusters`, `memberships` | Scope for gateway/worker/caller; explicit enrolled roles and revocation |
| `api_keys` | ID, cluster/owner, hash of random secret, revoked timestamp, request/concurrency quotas |
| `workers` | Stable installation ID, auth subject, cluster, session generation, capacity, draining/revoked state, server heartbeat time |
| `worker_models` | Worker, normalized name, digest, capability/version, metadata snapshot and observation time |
| `jobs` | UUID, protocol version, cluster/caller, endpoint, mode, state, request hash, model identity, current attempt, available/deadline times, worker lease, consumer lease, next/final sequence, byte counters, result reference/error, timestamps |
| `job_payloads` | Job FK, bounded JSON request; separate from frequently updated scheduling row |
| `job_attempts` | Job + attempt, worker/session, start/end, outcome, retry reason, progress/usage; no prompt copies |
| `job_chunks` | Primary key `(job_id, attempt_id, seq)`, UTF-8 text fragment, byte length, checksum; append-only |
| `job_results` | Job + winning attempt, bounded inline JSON or private object reference, checksum, content type, expiry |
| `idempotency_keys` | Unique `(caller_scope, key)`, canonical request hash, job ID, expiry |

Index queued jobs by `(cluster_id, model identity, available_at, created_at, id)` with a queued-state predicate; index running leases, consumer expiry, result expiry, and chunk cursor. Add nonnegative counters, legal state checks, unique memberships, FKs, and per-record byte constraints. Use server timestamps; do not trust worker clocks. Test query plans with mixed-model queues and large completed-job history. `SKIP LOCKED` is suitable for queue consumers, not strict global FIFO. [PostgreSQL locking](https://www.postgresql.org/docs/current/sql-select.html)

A live alias resolves to a digest/capability at submission; claiming must match it. Normalize aliases such as an omitted `:latest`; reject ambiguous same-name/different-digest registrations with a clear error until the operator selects the cluster's advertised variant. Do not choose one for `/api/show` and execute another. Availability and capacity are distinct; no eligible model, busy workers, and unavailable infrastructure are different internal errors, mapped through endpoint compatibility policy.

### RPC contracts

Every mutation validates role, cluster, ownership, protocol version, and applicable attempt/session token. Operator-configured maxima are enforced in SQL as well as in the gateway.

| RPC | Atomic responsibility |
| --- | --- |
| `submit_job` | Authorize caller/key, enforce bounded admission, resolve model, deduplicate key/hash, insert payload + queued job, reserve concurrency budget |
| `register_worker` / `update_models` | Bind enrolled auth subject to installation/session; rotate session generation; update validated inventory separately from heartbeat |
| `heartbeat_and_claim` | Refresh worker liveness, renew listed active leases, return cancellations; lock worker capacity, select eligible queued jobs with SKIP LOCKED, create attempt tokens and assign free slots |
| `append_output` | Lock job, check active attempt/lease/deadline, append contiguous bytes, update byte/sequence counters; identical retries succeed, conflicting content fails |
| `finish_job` | Commit final bytes or result reference, terminal state, final sequence, usage and quota release together; conditional on current attempt |
| `fail_attempt` | Store classified failure; requeue with available time or terminate according to retry policy; release capacity exactly once |
| `read_updates` | Read authorized jobs with supplied attempt/cursors; return bounded pages, terminal state and result descriptors; support batched cursor/consumer-lease acknowledgements |
| `read_metadata` / `backend_info` | Return authorized live-worker metadata and capabilities; report supported protocol/schema version without leaking credentials |
| `cancel_job` | Atomically set terminal cancellation, revoke attempt's mutation authority, release admission/capacity; repeated cancel is harmless |
| `reap_jobs` / `purge_expired` | Recover expired leases, expire abandoned synchronous requests/queued deadlines, and delete expired data in bounded batches |

Use consistent lock ordering across RPCs: idempotency/admission rows by stable key, then worker/session rows by ID, then job rows by ID. An operation that discovers a needed parent lock after locking a job must release/restart instead of acquiring locks in reverse order. A capacity lock may serialize claims for one worker; keep it short. Do not wait on a long transaction while Ollama runs. Duplicate `submit_job`, append, finish, cancellation, and retry transitions need explicit idempotent responses. Check deadline/lease expiry on every mutation, not only in a scheduled sweep.

Even when a client supplies no idempotency key, the gateway creates an internal submission-operation UUID and reuses it for ambiguous RPC retries. This deduplicates one HTTP attempt without pretending two separate client requests are identical. Caller-provided keys include the normalized request/endpoint/mode in their hash. For synchronous jobs, one active consumer token owns lease renewal and cancellation: an overlapping duplicate request gets an explicit conflict rather than stealing the lease or launching another inference. Completed duplicates may retrieve the retained result; async duplicates return the same job ID. A cursor acknowledgement means delivered to the gateway's response writer, not proof that the remote application processed it.

## 6. Execution, retries, and cancellation

```text
queued --claim--> running --finish--> succeeded
  |                  |
  |                  +--retryable failure, no published bytes--> queued (available_at)
  |                  +--failure / exhausted attempts-----------> failed
  +----deadline / cancellation / lost consumer-----------------> expired / cancelled
```

The same job can have multiple attempts, but only one current attempt has mutation authority. A terminal job never becomes running again. A deliberate retry after terminal failure creates a new job.

- Match the current five-retry ceiling as the initial configurable policy: at most six execution attempts, exponential delay capped at 60 s with jitter, and bounded by the request deadline. Classify transient connection failures separately from validation, unknown model, authorization, and malformed output. Do not retry permanent failures.
- **Never rerun a stream after the first durable output batch.** Retry lost RPC acknowledgements with the same attempt/sequence; that is transport retry, not new inference. This is a deliberate safety improvement over unrestricted retries of partially published Celery tasks.

The queue-wait budget applies to initial admission and each eligible retry wait.
Persist the requested budget and give a retry a new queue deadline measured from
its backoff availability, capped by the original total execution deadline. An
elapsed initial queue timestamp must not prevent recovery of already-running
work with no durable output.
- Lease expiry may cause duplicate physical computation. Fence all writes using job attempt and worker session, stop inference when lease renewal cannot succeed before expiry, and reject late finishes. Neither SQL claims nor PGMQ can promise exactly-once GPU execution. Celery's own late acknowledgement/redelivery also requires idempotency. [Celery task semantics](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
- Workers maintain control/heartbeat tasks independently of token production. Capacity counts running attempts; claims cannot exceed configured/enrolled capacity even with two concurrent claim calls. Local slots also stop new work while a cancelled Ollama call is still winding down.
- Closing a client stream triggers cancellation. For gateway crashes, synchronous jobs have a consumer lease renewed while the HTTP request exists. A worker seeing an expired consumer lease stops; a reaper catches abandoned queued jobs. Async jobs have no consumer lease and survive client/gateway exit.
- Graceful shutdown stops claims, marks draining, continues renewals while finishing jobs, then cancels after a configured grace interval. Test that closing the local Ollama HTTP request actually stops expensive generation; closing a socket alone is not proof of freed GPU capacity.

Starting values for measurement, not hosted guarantees: heartbeat/renew every 10 s, worker lease 45 s, consumer lease 60 s, reaper every 10 s, worker offline after 60 s. Worker liveness and each job's lease are separate. A successful heartbeat does not renew unreported jobs. Use one bounded database reaper task and a separately bounded purge task through [Supabase Cron](https://supabase.com/docs/guides/cron); monitor failed runs. Admission and workers still reject expired work if a scheduled run is delayed.

## 7. Streaming and large results

### Commit before forwarding

1. Submission persists a queued job; subscribing first is unnecessary because output is durable.
2. The worker serializes existing Ollama records once and accumulates fragments. Flush the first available record promptly, then batch around 200 ms or 64 KiB, whichever comes first. These are tuning defaults, not token limits.
3. `append_output` commits each batch before it is eligible for delivery. Split oversized records at UTF-8 boundaries; fragments need not end at NDJSON lines. Reassembly must preserve every byte and newline.
4. The request reader (or persistent FastAPI collector) fetches ordered cursor pages and feeds bounded per-request queues. Duplicate pages are skipped by sequence; missing pages are fetched again. HTTP chunk boundaries may change; NDJSON record content/order must not.
5. `finish_job` stores the genuine upstream terminal record and marks completion in one transaction. The gateway forwards success only after all preceding sequences are present. A lost terminal notification is recovered by polling.
6. On backend failure or deadline, send an explicit error if possible; never synthesize a successful `done` record. Missing durable data before retention expiry is an error, not successful EOF.

A worker HTTP timeout after append/finish may mean the transaction committed. Repeating the identical operation must retrieve its existing acknowledgement even if the job has since completed. Conflicting duplicates cannot rewrite history. Final-byte replay belongs to the winning attempt only. If Ollama unexpectedly reaches EOF without a terminal record, the worker marks the stream failed; it cannot translate EOF into success. Metadata/final-record parsing happens once in the worker, while the gateway forwards reconstructed bytes without parsing each token record again.

Each request reader acknowledges its cursor in its next scheduled read instead of one extra RPC per chunk. The persistent FastAPI collector batches multiple jobs and rotates fairly. A slow reader stops fetching; unconsumed data stays in the database within per-job byte/time budgets. While fetching is paused, a lightweight metadata-only `read_updates` call renews the consumer lease independently; a configurable reader-stall timeout cancels the job rather than keeping it alive forever. Enforce both memory and total stored-output limits. Durable replay repairs backend reconnects **while the original HTTP request survives**; it cannot transparently reconnect an Ollama client after the gateway process dies.

### Size and retention policy

The Edge limits page does not specify a request/response body cap. That is not evidence of unlimited sizes. Measure gateway, PostgREST, serialization, memory and client behavior. Embeddings are especially important: 32 × 768 float32 values occupy 96 KiB raw, but at an assumed 12 JSON bytes/value they occupy 288 KiB before envelopes.

Initial configurable Supabase test values: 1 MiB request, 256 KiB inline result, 64 KiB chunk, 256 KiB read-page data, 1 MiB gateway output buffer, and 32 MiB total output/result. These bound resources; tune against representative fixtures before publishing support. The current Edge synchronous response limit is 1 MiB. Storage does not bypass a response that still travels through the gateway. Async explicit downloads can use a separate, larger stored-result budget. See [hosting limits](installation.md#hosting-profiles). Do not impose these new limits silently on legacy Redis deployments. Reject known oversized requests before queueing; enforce unknown output growth during inference and report explicit failure.

RPC responses are bounded to 2 MiB. Claim admission accounts for the full encoded
envelope, assignment metadata and stop entries before changing job state; jobs
that do not fit remain queued for a subsequent claim. Claim receipts store
assignment identity without prompt copies and reconstruct only authorized live
attempts on replay. Payload expiry therefore does not leave raw prompts in
receipt retention.

For larger non-streamed results, the worker uploads the exact JSON response to a private Storage object, then conditionally finalizes the job with size/hash/reference. The gateway validates/fetches that object and returns the original JSON body, **not a download URL**, on Ollama routes. Object upload happens outside the database transaction: use deterministic attempt-specific paths, verify completion before finalizing, tolerate identical retries, reject stale attempts, and garbage-collect unattached objects. Storage adds a network hop and must be included in latency/cost tests. [Storage authorization](https://supabase.com/docs/guides/storage/security/access-control)

Retain stream data through active consumption; after terminal delivery acknowledgement, purge content on a configurable short grace period (initially 60 s). Unacknowledged terminal results expire after 30 minutes by default, matching the current configured Celery result lifetime. Async jobs use an explicit longer retention (initially 24 hours). Set expiry from completion, not submission. Preserve idempotency tombstones through the advertised retry window even after content expires; expired results return an explicit expiry response, never silently launch fresh inference. Purge payloads once no retry can require them. Deletion from active tables does not erase backups, WAL, logs, or downloaded data.

## 8. Authentication, authorization, and worker enrollment

Start with private, operator-enrolled clusters. Workers necessarily see prompts and can fabricate answers; an open public worker marketplace is not solved by RLS.

- Existing Redis API authentication behavior remains unchanged. Supabase gateways default to high-entropy `osk_...` application keys stored as hashes, scoped to a cluster/caller, with revocation and quotas. A trusted-network FastAPI installation may explicitly opt into anonymous access to one configured cluster; Edge deployments require keys; reject anonymous mode on public hosted profiles. Authentication configuration is an intentional difference, not an invisible API change.
- The API process/Edge gateway holds the project secret in server-side configuration, invokes narrow gateway RPCs, and validates caller keys there. Never return or log project credentials. A privileged gateway is a trust boundary; keep worker-facing requests on publishable-key + user-JWT clients, never a privileged client reused with mutable auth state.
- Provision a separate Supabase Auth identity per worker installation; the operator grants cluster membership and capacity. Add a worker-login/enrollment command that obtains and securely stores its refresh session. No recurring password login per job. Refresh in one control task, persist rotated refresh tokens atomically with restrictive permissions, and fail clearly when re-enrollment is required. [Auth sessions](https://supabase.com/docs/guides/auth/sessions)
- `register_worker` derives identity from the JWT and enrollment. The client cannot choose another owner's worker ID, role, cluster, or capacity. Every RPC checks enrollment/revocation and current session generation; auth subject alone is insufficient.
- Restrict required `SECURITY DEFINER` functions with fixed search path, qualified names, a least-privileged owner, and explicit EXECUTE grants. Check row authorization inside them because definer functions may bypass RLS. [Database function security](https://supabase.com/docs/guides/database/functions)
- Storage policies allow only authorized job/attempt objects. Private Realtime hints require policies and no prompt/token content; cached channel permission must not be treated as current job authorization. Durable reads/mutations recheck access. [Realtime authorization](https://supabase.com/docs/guides/realtime/authorization)

Add credential-redacted logs and tests for two users/clusters, revoked workers, forged attempts, unknown API keys, private objects, and direct Data API access. Hosted data residency/retention and trust in worker hardware remain deployment choices.

## 9. Efficiency and operations

Keep the protocol simple and tune measured bottlenecks:

- Reuse HTTP connections within each Edge request, and use one long-lived pool per FastAPI/worker process. Keep caller auth/context request-local. Use short RPC transactions, no `pg_sleep` long polling and no database connection per active inference. Default authenticated queries time out after 8 s; that is a statement limit, not a job-duration limit. [Database timeouts](https://supabase.com/docs/guides/database/postgres/timeouts)
- Batch active-job reads across requests in persistent FastAPI, lease renewals across worker slots, and contiguous output fragments in every mode. Edge requests retain independent cursors. Poll active output around 200 ms; back off after repeated empty reads and wake early on optional hints. Page by rows **and** bytes.
- Claim only with free slots. Idle workers use jittered backoff (0.5 to 5 s initially); optional queue hints allow a longer periodic fallback. At 100 idle workers, polling once/s creates 8.64 million calls/day; every 5 s still creates 1.73 million. Include this cost explicitly.
- Send liveness/capacity every 10 s; refresh `/api/ps` on its freshness schedule; compare inventory/digests before resending `/api/show` data. Do not recompute and upload every model's full metadata every heartbeat, as the current worker does.
- Index before scaling. Keep payloads/chunks out of hot scheduling rows, delete in bounded batches, observe autovacuum/WAL/index growth, and add partitioning only when measured volume warrants it. No per-token database updates.
- Place gateways near the database when deployment controls allow it; measure Edge region selection rather than assuming that proximity to the caller minimizes the worker round trip. Locate workers with network latency in mind. Start the persistent FastAPI deployment with one process and validate real multiprocess startup before advertising scale. The current Uvicorn setup passes an app object, so its `WORKERS` setting alone is not evidence of multiprocess operation.

At a 200 ms flush interval, one continuously producing stream creates about 5 append transactions/s. Twenty streams imply about 100 appends/s before leases/completions. With default Edge readers, those twenty streams can add about 100 read RPCs/s while actively producing; do not apply the FastAPI multiplexing estimate to separate Edge invocations. A persistent FastAPI collector reading 32 active cursors per call might use 5 calls/s for those streams, but still reads all their bytes. At 200 output bytes/token and 20 tokens/s, that same stream emits ~4 KB/s, or ~240 KB/minute before indexes/WAL. These are capacity-model assumptions, not benchmarks. Measure actual serialized records, RPC counts, storage, egress, database CPU and tail latency.

Realtime limits remain relevant to optional hints, but do not determine total response size. Current Free/Pro-with-cap allowances are 100/500 messages/s, 200/500 concurrent connections, and 256 KB/3,000 KB Broadcast payloads. Overload may disconnect connections. [Realtime limits](https://supabase.com/docs/guides/realtime/limits) Billing counts a send plus each recipient, so cluster-wide fanout is not free. [Message accounting](https://supabase.com/docs/guides/platform/manage-your-usage/realtime-messages)

Monitor queue age by model, lease expiry/retries, active workers/slots, append/read latency, token relay lag, unread bytes, cancelled GPU time, result bytes/retention, storage orphans, RPC/Auth errors and schema version. `/health` retains its existing liveness response; add `/ready` for backend/schema reachability, without requiring an available model or generating inference.

## 10. Default Edge gateway and asynchronous jobs

Hosted Edge worker lifetime is **150 s Free / 400 s paid**, request idle timeout is **150 s**, memory is **256 MB**, and CPU time is **2 s per request excluding asynchronous I/O**. A worker can serve several requests; do not promise a fresh full lifetime for every one. [Edge limits](https://supabase.com/docs/guides/functions/limits)

Streaming and `waitUntil()` do not remove the hard cap. Tie the relay promise to the request/stream lifecycle, propagate aborts, and close subscriptions/timers in `finally` so open sockets do not outlive a finished request. Model loading, queue wait, prompt evaluation, output generation, serialization and transfer all consume the available lifetime. Repeated RPC JSON decoding and stream callbacks also consume the small CPU budget, even while inference runs elsewhere. [Runtime behavior](https://supabase.com/docs/guides/troubleshooting/edge-functions-worker-timeouts-and-websocket-drops)

```text
available generation time = relay lifetime - setup/queue/load/prefill - transfer/shutdown margin
approximate token budget = max(0, available generation time) × measured tokens/second
```

For illustration, 20 s setup/load/prefill and 15 s reserve leave 115/365 s: at 5 tokens/s, about 575/1,825 generated tokens; at 20 tokens/s, 2,300/7,300. These are arithmetic, not capacity promises. Reasoning and long prompts can exhaust time before useful output. Slow models remain usable when their whole request fits a gateway budget, or through async jobs. Longer persistent FastAPI budgets remain planned; the current SQL candidate keeps its 135-second maximum.

The default Edge function is a thin adapter to the same RPCs, cursors, error/result contract and protocol fixtures. Set `verify_jwt = false` for custom application keys and authenticate protected routes in the handler; implement path prefixes and CORS explicitly. [Function authentication](https://supabase.com/docs/guides/functions/auth-headers)

Initial non-streamed Edge support must complete below the idle boundary with a margin, including on paid plans. Candidate starting budgets are 135 s non-streamed and 135 s Free / 385 s paid streamed, always reduced by elapsed setup and any smaller caller/operator limit; reused-worker measurements can require shorter deadlines. These are application targets, not a guaranteed remaining isolate lifetime. Test early headers separately; do not base compatibility on unverified gateway behavior or introduce heartbeat bytes into ordinary JSON. Set measured conservative deadlines, reject inadmissible work, cancel at deadline, and never fake `done_reason: length` or success. Self-hosting can change runtime settings, but operating the broader Supabase stack is an alternative deployment, not an extension of hosted limits.

The self-hosted runtime configuration is visible in the [official function supervisor](https://github.com/supabase/supabase/blob/master/docker/volumes/functions/main/index.ts); proxy timeouts and resource limits still need deployment-specific validation.

The FastAPI gateway has its own configurable request/queue/read-stall deadlines and must not inherit the Edge cap. Its persistent profile must fit the implemented SQL maximum; increasing that maximum requires separate qualification. See the [hosting profiles](installation.md#hosting-profiles). Apply gateway policy at submission, persist the resulting absolute deadline, and let workers enforce it without needing to know which gateway submitted the job. API keys may be shared across the two gateways; quotas are enforced against their shared cluster state.

A local HTTP mock verified that `ollama==0.6.3` retains the function path and sends `OLLAMA_API_KEY`. A second mock verified that its streaming iterator can finish **without error when a valid NDJSON body ends without `done: true`**. Therefore a forced platform close is not guaranteed to be reported by stock client code. Hosted Edge cannot promise transparent recovery; document the completion-check requirement and test abrupt as well as graceful closes.

Include the explicit asynchronous extension in the default Supabase release for work that should outlast the HTTP client or hosted request budget:

| Route | Contract |
| --- | --- |
| `POST /jobs` | Endpoint + original payload + limits; optional `Idempotency-Key`; returns 202, job ID and Location |
| `GET /jobs/{id}` | Authorized state, attempt/progress, terminal error, result availability/expiry |
| `GET /jobs/{id}/result` | 202 while pending; exact JSON for bounded results or explicitly requested signed download; 410 after retention expiry |
| `DELETE /jobs/{id}` | Idempotent cancellation; completed jobs return their unchanged terminal state |

The [async v1 policy](development-handoff.md#async-v1-policy) fixes stream handling, caller visibility, terminal/expiry statuses and explicit download behavior. Freeze its exact JSON schemas and fixtures before implementing the public extension.

Async endpoint selection is an allowlist of the inference operations implemented by the worker, using the same payload validation. Never turn `/jobs` into arbitrary URL/method execution against Ollama, Supabase, or the worker's local network.

New infrastructure/admission errors use a consistent `{"error":"..."}` body: 401/403 authentication/authorization, 409 conflicting idempotency or active consumer, 413 request too large, 429 quota/concurrency/queue admission, 503 unavailable backend, and 504 an application deadline before headers. Preserve the route-specific legacy worker-error mappings in the compatibility matrix. After a stream starts, status is already committed; send an NDJSON error and close, consistent with [Ollama's error protocol](https://docs.ollama.com/api/errors). The async status/result contract exposes a terminal failure with its error details rather than serving it as a successful Ollama result.

Async submission returns immediately; the external worker continues without an Edge invocation kept alive. It is not `waitUntil()` inference, and is not a transparent replacement for an Ollama route. [Background-task limits](https://supabase.com/docs/guides/functions/background-tasks) Give async inference its own configurable execution deadline and retention, and enforce the same capacity/quota constraints as synchronous jobs.

## 11. Delivery sequence

October 31 priority update: the [CLI onboarding milestone](cli-onboarding.md)
now drives implementation over the existing backend and operator engine. Deliver
share/join, provider create-or-select setup, ongoing operation and installed-package
user acceptance before hosted OAuth/buttons or expanded benchmarks. The delivery
sequence below describes underlying backend dependencies; completing those pieces
does not establish the finished one-command user journey. Provider creation,
Management SQL, user services and candidate
publication tooling now exist in source; integrated/fresh/published acceptance is
tracked separately. Existing contracts and qualification gates still apply.

Each step is reviewable and leaves Redis operational. Migrations are additive; runtime support follows tested protocol versions.

| Step | Concrete changes | Exit evidence |
| --- | --- | --- |
| 0. Characterize and spike | Shared route fixtures; fake Ollama; hosted Edge claim/relay; installer API/CLI capability probe | Edge limits and CPU/I/O evidence; verify OAuth project access and migration path before promising browser installation |
| 1. Extract shared contracts/executor | Protocol schemas and fixtures; shared executor under `worker/`; backend-specific config/imports; Redis stays operational | Existing Redis tests/defaults pass; no Celery initialization when selecting Supabase; Deno validation matches fixtures |
| 2. Implement database protocol | Versioned migrations, exposed RPC schema, enrollment, policies, quotas, state machine, cleanup | Concurrent claim/capacity/idempotency, role-denial and stale-attempt tests; fresh installation and upgrade tests |
| 3. Complete Edge non-streamed slice | `supabase/functions/ollama`, worker loop, login/session handling; System One, legacy embeddings and metadata included | Same deterministic results/errors as existing routes within Edge deadlines; real local Ollama smoke runs |
| 4. Add streaming and async | Durable chunks/cursor replay, bounded buffers, finish/cancel reconciliation, Storage path, async submission/status/result/cancel | Exact stream bytes, lost acknowledgements, slow readers, large records; async job exceeds 400 s and result survives reconnect |
| 5. Add FastAPI + Supabase option | Backend interface, Redis wrapper and Supabase adapter; route/lifespan injection; persistent collector | Both FastAPI backends pass route matrix; System One thread affinity; longer synchronous budgets remain a future target |
| 6. Deliver installation flows | Shared resumable bootstrap engine and CLI fallback; Supabase OAuth installer; owner/key/worker onboarding | Fresh install, existing project, interrupted/resumed upgrade, preview isolation, no secrets leaked; all installation gates V16–V21 pass |
| 7. Release and operate | Deployment/env examples, startup probes, metrics, dependencies, drain/upgrade/rollback; hosted soak/load/security tests | Edge documented as Supabase default; FastAPI option tested; declared workload limits/costs; Redis regressions pass |
| 8. Broaden API/optimize | Modern embed, tools/structured fields, coordinated upstream-parity fixes; measured hints/inline relay/LISTEN/cache improvements | Shared fixtures pass across gateways/backends; limits explicit; optimizations justified by profiles |

Avoid parallel implementations of authorization, claiming, retries, and retention in Python and TypeScript: SQL RPCs own those transitions. Python owns the shared execution/API boundary; TypeScript adapts HTTP in the default Supabase gateway. Declare imported HTTP/Auth/Storage libraries directly in a Supabase extra or base dependencies; synchronize `pyproject.toml`, `setup.py`, and the development requirements strategy. Do not reorganize existing installation extras as an unrelated prerequisite.

### Release gates and benchmark method

The [verification matrix](compatibility.md) gives correctness cases and exact existing-test limitations. Required evidence includes:

- Every current route and supported payload, with equivalent successful JSON/NDJSON and tested error mappings; no mandatory changes to existing Redis deployments.
- Durable append/replay/finish correctness under concurrent claims, retries, transport acknowledgement loss, notification loss, worker death, gateway death, cancellation, clock skew, and revoked credentials.
- Zero cross-cluster/job access in adversarial policy tests. No successful terminal response with missing bytes; no accepted writes from a fenced attempt.
- At least 1,000 deterministic short jobs plus targeted long/fault cases, with exact byte/result checks. Do not compare sampled model prose to establish protocol correctness.
- Proposed initial performance targets at 1 and 5 active streams: p95 additional first-token latency <=750 ms versus Redis and p95 batch relay lag <=500 ms, with no sustained backlog. These are targets to validate, not measurements. Test 10 and 25 streams on the intended paid project and publish the measured sustainable point, not a guessed concurrency promise.
- Memory remains bounded by configured per-request limits and the admitted request count; a slow consumer cannot grow gateway memory without bound. Explain database cost per 1,000 one-minute streams and per idle worker-day, including retention and egress.
- Real models: warm/cold loads, short/long context, constrained output, legacy embeddings, System One, worker contention; record p50/p95/p99, versions, region, plan and hardware.
- Installation: hosted CLI, future browser setup and recovery pass [V16–V21](compatibility.md); published buttons target tested release artifacts, and readiness distinguishes infrastructure from available inference.
- Default hosted Edge: delayed first byte, ~200 s non-streamed response on paid, steady output/idle gaps/reused workers, runtime/CPU/memory cutoffs and exact final-marker behavior. Body-size matrix: 64 KiB, 256 KiB, 1 MiB, 5 MiB and configured maxima. A successful long FastAPI request does not prove that Edge supports the same duration; gate each gateway separately.

If transport durability is correct but database cost/latency misses the target, measure coalescing, reads, indexes and notification tuning first; evaluate the direct relay only with evidence. If Edge cannot safely support the advertised envelope, keep the Supabase default experimental and report the failed gate; the FastAPI option remains useful but does not by itself complete the default Edge release. Do not reduce the mandatory existing-route scope to make the release pass. Accepted limits must be explicit and produce useful errors or the documented async alternative.

## 12. Deployment, upgrade, and rollback

The [installation plan](installation.md) specifies the user journeys, bootstrap state machine, hosting limits, ownership and recovery. Create one Supabase project per trust/operational boundary initially. Apply reviewed migrations and API schema configuration using an administrative deployment identity; ordinary runtime identities cannot migrate or grant membership. Set up cleanup scheduling and verify it with expired fixtures. Provision operator cluster, gateway secret/application keys, worker identities/enrollment, private result bucket, and a protocol/schema version.

Deploy the default Edge gateway and verify its schema compatibility check; optionally boot FastAPI against the same project. Then start one worker, run a non-streamed job and a stream, kill/restart the worker, and verify cleanup. Scale only after measured health. `start-api --backend supabase` and worker startup must fail clearly on missing dependency, wrong schema version, unavailable Auth, or missing enrollment. Avoid health probes that enqueue billable inference.

For upgrades, expand schema/RPC compatibility first, deploy gateways/workers supporting both active protocol versions, drain old workers, then remove obsolete contracts in a later release. Session generations fence an old worker process after restart. Add admission limits before load testing; pending jobs have queue deadlines even when all workers disappear.

Rollback selects the existing Redis gateway/worker deployment for new requests and drains or explicitly cancels Supabase jobs. Never copy in-flight jobs across queues implicitly. Preserve Supabase results until retention expires, then retire resources deliberately. Restoring a database backup can resurrect stale jobs: restart with new worker sessions, reconcile leases/consumer ownership, and invalidate old signing/session assumptions as appropriate before reopening admission.

## 13. Definition of plan completion

This document, its linked matrix and the installation plan cover architecture choice, all existing properties, limits and failure modes, implementation contracts, security, cost, configuration, release verification, deployment and rollback. Runtime numbers are sourced or explicitly marked as assumptions. Hosted experiments and new backend tests are **implementation work**, not claimed completed research.

The gateway priority is decided: Edge Functions by default for Supabase, optional FastAPI using the same backend, and the existing Redis deployment retained. The remaining workload-specific tuning is deliberately measurable: model/hardware mix, desired concurrency, output sizes, retention and cost ceiling. Until those are supplied, use the stated candidate values and benchmark matrix. No unresolved product choice permits omitting an existing API route from the Supabase backend.
