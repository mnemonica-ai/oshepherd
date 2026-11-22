# Supabase wire protocol v1

The candidate worker reserves separate inference (64) and metadata (four)
connections. Individual metadata requests have a five-second budget and a full
inventory cycle has a 25-second budget; invalid inventory preserves the last
good values until their SQL freshness expires. These budgets do not limit
inference reads or replace independent heartbeat and Auth refresh.

Status: experimental implementation contract. Python/TypeScript clients and SQL
must use these schemas. Bounded hosted measurements exist, while full resource
qualification remains open; see the [progress ledger](../../../docs/plans/supabase/progress.md). This
version is separate from the legacy characterization fixtures in `protocol/v1`.

## Envelope, identity and error conventions

PostgREST calls are `POST /rest/v1/rpc/<name>` with `Content-Profile` and
`Accept-Profile: oshepherd_api` and a body of `{"p_request": <request>}`.
Each request contains integer `v: 1` and a UUID `operation_id`; each successful
RPC returns `{"v":1,"data":{...}}`. `rpc.schema.json` defines request fields
for all 22 functions, including three release-candidate operator RPCs. Unknown fields are rejected; null is permitted only where the
schema says so. Missing optional fields use the documented defaults.

IDs are UUID strings. Wire timestamps are UTC, encoded exactly as
`YYYY-MM-DDTHH:mm:ss.SSSZ`. Scheduling durations and requested deadlines are
integer milliseconds; retained-content/idempotency lifetimes are integer
seconds. Ollama duration fields retain nanoseconds. Sequence numbers and all
byte counters are nonnegative integers; chunks start at sequence 1 and cursor
0 means nothing delivered. Byte counts and lowercase SHA-256 checksums refer
to **UTF-8 bytes**, including JSON whitespace and NDJSON newlines.

SQL failures use PostgREST `PT<HTTP status>` errors with a stable snake-case
`message`. Gateways expose `{"error":"<code>"}` for new platform errors,
with the corresponding status. Legacy inference worker errors retain their
characterized JSON 500 convention. Validation in the new gateways returns
422 before admission. An error after NDJSON headers is an NDJSON error record
followed by close; it is never a successful terminal record.

Stable categories include `invalid_request`, `unsupported_protocol`,
`unauthorized`, `forbidden`, `not_found`, `worker_revoked`, `stale_session`,
`stale_attempt`, `lease_expired`, `execution_expired`, `consumer_expired`,
`admission_limited`, `model_not_available`, `ambiguous_model`,
`idempotency_conflict`, `consumer_conflict`, `sequence_conflict`,
`output_too_large`, `result_too_large`, `result_expired`, `upstream_error`,
`incomplete_upstream_output`, `cancelled`, `backend_unavailable` and
`protocol_mismatch`. Error details/logs must not contain application keys,
Auth tokens or prompts. A retryable admission/rate error uses HTTP 429 with
a finite Retry-After; caller retry must reuse its idempotency key.

## Retry identity

The gateway creates an operation UUID once per HTTP submission and retains it
through transport retries. Caller-supplied `Idempotency-Key` values are 1–128
characters; their scope is the authenticated application key/caller. Without
one, SQL uses the submission operation UUID. Keys cannot move between callers.

SQL computes the canonical request hash from PostgreSQL's `jsonb::text` UTF-8
encoding of `{request, mode, limits}` after gateway normalization. Object order
does not matter; arrays, explicit nulls and requested limits do. Clients do not
compute an independently formatted canonical hash. Relative requested budgets
are hashed; generated server timestamps are not. A changed request under the
same key is 409. Successful replay returns the original job even when its
content has expired; it never causes another inference.

Registration deduplicates `(enrolled worker, operation_id)` and does not rotate
its session on an identical retry. A deliberate restart uses a new operation
UUID and fences its predecessor. Heartbeat/claim exposes current assigned
attempts after an ambiguous response; worker capacity is locked transactionally.
Claim pages are also limited by their complete encoded RPC response size before
jobs are assigned. Receipts retain assignment metadata without request payloads;
identical live-attempt replay reconstructs requests from the jobs table and
preserves the original envelope. Terminal or superseded attempts reject replay
with `stale_attempt`. Applying the spike upgrade strips payloads from existing
claim receipts while retaining their operation IDs and hashes.
Append deduplicates `(job, attempt, seq)` and checksum/content. Finish and
classified failure store operation receipts so identical replay retrieves its
acknowledgement after commit. Revocation/session fencing still applies to
replayed calls; no old identity regains mutation authority.

## Auth and roles

| RPCs | Data API identity | Additional SQL checks |
| --- | --- | --- |
| `bootstrap_cluster`, `create_api_key`, `enroll_worker`, `revoke_worker`, cleanup, `installation_info`, `revoke_api_key`, `disable_cluster` | `service_role` | Explicit selected cluster/operator; no public bootstrap. The final three require versioned release migrations |
| `submit_job`, `read_updates`, `cancel_job`, `read_metadata`, `backend_info` | `service_role` gateway | Validate opaque application key hash, cluster and caller ownership each call |
| `register_worker`, `update_models`, `heartbeat_and_claim`, `append_output`, `finish_job`, `fail_attempt` | `authenticated` worker | JWT subject bound to an operator-enrolled worker, revocation, current session/generation and attempt |
| Every inference/control RPC | `anon`, missing JWT, wrong role | Denied; never fall through to privileged access |

Storage tables live in private `oshepherd`, with RLS and no ordinary direct table
grants. RPC functions have qualified names, fixed search paths and explicit
EXECUTE grants. Their non-login owner receives only the permissions needed by
the protocol; functions enforce row authorization themselves. Private result
objects have current-attempt worker write policies and gateway-authorized reads.

The experimental `maintenance_tick` and `complete_maintenance` functions are
the narrow exception to runtime ownership: the migration identity owns them so
they can inspect Storage metadata. Only `service_role` can call them. Neither
worker identities nor the non-login runtime owner receive Storage SELECT or
DELETE privileges. Private scheduled sweep functions remain runtime-owned and
are not granted to Data API roles.

Application keys contain 256 random bits, prefixed `osk_`, and the database stores
only their SHA-256 hashes. Private client exports and the operator's named-client
registry retain raw keys for use and re-export; see [client onboarding](../../../docs/supabase-client-walkthrough.md).
Hashes are never returned by runtime RPCs. Membership is
bound to the authenticated caller scope rather than knowledge of job IDs.

An authenticated operator provisions a confirmed Auth user with no reusable
worker password, binds its subject/cluster/capacity through `enroll_worker`,
then generates a single-use Auth token using the administrative
[generateLink API](https://supabase.com/docs/reference/javascript/auth-admin-generatelink).
The code is transferred via a protected enrollment file or secure prompt, never
as a URL or command-line argument. The worker calls
[verifyOtp](https://supabase.com/docs/reference/javascript/auth-verifyotp) using
`token_hash` and `type: "email"`, then stores access/refresh tokens atomically
in an owned 0600 file. Refresh runs in one control task and does not register
a new worker generation. Public signup and SMTP are unnecessary: this exact
flow passed against local Auth with public signup disabled and no mail service.
Enrollment expiry is configured to 600 seconds. A consumed/expired code is
rejected; acknowledgement loss during the one-time Auth exchange requires
operator recovery. No blind exchange retry or duplicate identity provisioning.
Database revocation denies all runtime calls even while an old JWT remains valid.

## Finite experimental limits

SQL enforces maxima, regardless of gateway configuration. A gateway may reduce
budgets according to its hosting profile. These are experimental defaults.

| Limit | Default / maximum |
| --- | --- |
| Request UTF-8 bytes | 1,048,576 |
| RPC response / complete claim page UTF-8 bytes | 2,097,152, including envelope and stop entries |
| Completion RPC envelope bytes | 4,194,304; permits JSON-escaped final-record fragments |
| Upstream NDJSON record / final-record bytes | 1,048,576 |
| Chunk bytes | 65,536 |
| Read-page bytes / cursor count | 262,144 / 32 |
| Inline result bytes | 262,144 |
| Edge ordinary JSON result retrieval bytes | 1,048,576; larger async success uses download |
| Total stored output/result bytes | 33,554,432 |
| Worker enrolled slots | Default 1; maximum 64 |
| Cluster queued / running / active jobs | 64 / 64 / 128 |
| Per-caller active jobs | 4 |
| Sync / async queue budget | 30,000 / 60,000 ms |
| Edge sync total budget | 135,000 ms; paid streaming override needs measured profile |
| Async execution budget | 3,600,000 ms; maximum 86,400,000 ms |
| Heartbeat / worker lease / consumer lease / offline | 10,000 / 45,000 / 60,000 / 60,000 ms |
| Reader stall | 30,000 ms |
| Maximum execution attempts | 6 (five retries); delay capped at 60,000 ms |
| Unacknowledged sync / async result retention | 1,800 / 86,400 seconds after completion |
| Idempotency/content-expiry tombstone | 172,800 seconds after terminal completion |
| Signed download | 300 seconds maximum |
| Maintenance page / operation receipt | 32 objects by default, maximum 64 / two days |
| Orphan minimum age / deletion grace / deletion lease | 600 / 300 / 45 seconds |

`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SCHEMA` select transport.
Worker `WORKER_SESSION_FILE`, `CONCURRENCY`, `HEARTBEAT_SECONDS` and
`DRAIN_SECONDS` affect the local loop. `PREFETCH_MULTIPLIER` must be 1.
Ollama host precedence is `OLLAMA_BASE_URL`, `OLLAMA_HOST`, localhost. New
worker configuration cannot accept an administrative key. CLI/backend
precedence is explicit flag, `OSHEPHERD_BACKEND`, `redis`.

## Output, state and async API

Worker execution preserves the characterized SDK response serialization. Missing
or null stream values select non-streamed execution, and the new executor sends
explicit `stream: false` upstream rather than inheriting Ollama's HTTP default.
System One always uses its raw non-streamed path with extras preserved.

Append persists exact bounded fragments before readers receive them. The worker
holds the genuine final NDJSON record for `finish_job`, which commits all its
fragments and the successful terminal state in one transaction. Finish cannot
hide a sequence gap. Incomplete EOF is failed, not successful. An upstream
failure is retryable only before any output is durable, before the total
deadline, and below the attempt ceiling. Revoked/expired attempts cannot append,
renew or finish. Queue and total deadlines are separate server timestamps.
The submission's `queue_ms` is stored as the queue-wait budget. Initial admission
expires after that wait; an eligible execution retry receives a fresh wait
starting at its backoff `available_at`, capped by the unchanged total execution
deadline. Backoff never makes a job claimable early. Existing spike jobs recover
their effective initial queue budget from their original deadline/creation
timestamps when upgraded.

Successful JSON results are either inline UTF-8 content or an attempt-specific
private Storage object, with size, checksum and content type. URLs are generated
only after current caller authorization; durable results store object references,
not signed URLs. Result expiry does not change `succeeded` to execution-expired:
it leaves a content-expiry tombstone for the advertised retry window.

`POST /jobs` accepts `{endpoint,payload,limits?}` for the four inference paths.
Generate/chat reject explicit streaming with 422; metadata is not admitted.
Response 202 contains `{id,status,status_url}` and a Location header. Submission
disconnect preserves an accepted async job. `GET /jobs/{id}` returns the durable
status object in `async.schema.json`, including failures. Other-caller/unknown
IDs return 404 after authentication. `DELETE` is idempotent and returns the
winning terminal status; cancellation races atomically with finish.

Result retrieval returns 202 pending, exact original JSON on retained success,
409 for failed/cancelled/execution-expired work, and 410 for expired content.
An oversized successful result returns `result_too_large` with 413 while leaving
the successful job downloadable. `?download=true` explicitly returns
`{url,size_bytes,sha256,content_type,expires_at}`. The URL is a short-lived bearer
credential. Async work has no consumer lease and survives the submitting
gateway invocation; admission, attempt and deadline policies still apply.

## Durable cleanup

Expiry records an attempt-specific deletion intent before dropping the result
reference or tombstone. A purge acknowledgement is not the deletion queue.
Each purge visits a bounded job page and deletes at most 64 chunks and 64
attached worker receipts per job; additional passes finish large streams.
Content expiry preserves the terminal state and submission tombstone.

`maintenance_tick` requires `{cluster_id,limit}` and returns
`{reaped,purged,scanned,enqueued,claim_id,object_paths}`. It replays an identical
operation UUID, scans one cluster's Storage namespace using its bytewise index,
and preserves referenced objects and live current attempts. Old unreferenced
objects enter the durable queue with a five-minute grace. Due paths receive a
45-second claim lease; a crashed runner's lease expires for recovery.

The operator deletes bytes through the Storage HTTP API, then calls
`complete_maintenance` with `{cluster_id,claim_id,deleted_paths,failed_paths}`.
The two arrays contain at most 64 unique paths combined and cannot overlap.
Every canonical UUID/UUID/UUID/result.json path must belong to that cluster.
SQL rejects a superseded claim with `stale_cleanup`, verifies that reported
deletions really disappeared from Storage metadata, and returns
`{deleted,retrying}`. Failure or ambiguous HTTP acknowledgement retains intent
with bounded backoff. Identical completion retries retrieve the receipt.

`supabase-maintain --env-file <private-operator-file> [--once]` runs independently
of workers and gateways. It needs the server-only project key, selected cluster,
schema and URL. `MAINTENANCE_SECONDS` defaults to ten (10–3600 permitted) and
`MAINTENANCE_BATCH` defaults to 32 (1–64 permitted). Routine diagnostics contain
only aggregate counts and stable error codes. Database Cron separately reaps
and purges expired SQL content; pending object deletion remains durable while
the HTTP runner is offline. Local verification and scheduling commands are in
[the maintenance evidence report](../../../docs/plans/supabase/experiments/local-maintenance.md).
