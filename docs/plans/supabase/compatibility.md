# Supabase backend: compatibility and verification matrix

Companion to the [implementation plan](implementation.md), inspected October 4, 2026. This is a specification and implementation test checklist. Unless explicitly marked as locally verified below, acceptance tests are **planned**, not passing or implemented.

Current evidence is recorded in the [progress ledger](progress.md), updated
October 14, 2026. The latest combined Python selection has 429 passes, one strict
local Edge disconnect failure and two opt-in real-model cases deselected;
subsequent probe guards are recorded separately in the ledger. Deno has 48 passing
contracts. Both hosted profiles pass real inference/routes/downloads, measured
135-second synchronous deadlines, async work beyond 400 seconds, 1,000-job loads,
actual Auth refresh/recovery and bounded size probes. Installed-wheel local CLI
onboarding and actual provider adoption/deployment/removal have acceptance
evidence. Both hosted disconnect checks fail. The approved test gateways are now
removed; see [independent acceptance](experiments/independent-acceptance.md).
The broad matrix remains an acceptance specification; full provider ceilings,
JavaScript SDK parity, performance/cost/resource limits, fresh hosted/browser
onboarding and OAuth/Marketplace/public button flows remain unverified.
The [next-steps checklist](next-steps.md) orders the remaining gates.

The later adoption-first CLI slice has **454 passing offline tests**, **24 passing
focused local integration tests**, and actual installed-wheel share/invite/join
with real Ollama JSON/NDJSON, restart and foreground shutdown. Its
[acceptance report](experiments/cli-onboarding.md) distinguishes existing local
installation evidence from mocked provider onboarding and unrun fresh hosted setup.
It does not close the strict disconnect or browser installation gates.

The October 31 `0.0.25a1` source adds provider creation/selection, Management SQL,
Vercel REST onboarding and user services. These changes have separate contract and
candidate-package checks in the ledger. Final fresh/live acceptance and actual
published-package validation are pending consolidation; the matrix's unverified
cases stay open. Candidate publication requires an explicit manual gate and does
not qualify either profile.

## Product contract

Supabase's default gateway is an Edge Function with declared time/size/concurrency limits. FastAPI with the same Supabase backend supports Vercel and persistent hosting profiles. Persistent hosting is the option for long synchronous work; Vercel has separate finite duration and payload limits. See the [installation plan](installation.md). Existing FastAPI/Celery/Redis installations remain supported with their current CLI defaults. All existing routes are required; Edge limits are accepted and explicit, not grounds to omit a route. Async jobs provide a default-gateway path for longer work and require a different client flow.

## Existing route behavior

Paths below point to the authoritative current code. Compare backend behavior using deterministic upstream fixtures; model-generated text is not stable enough for exact comparison.

| ID | Route and evidence | Current behavior to preserve initially | Supabase acceptance |
| --- | --- | --- | --- |
| C01 | [`POST /api/generate`](../../../oshepherd/api/generate/routes.py), [model](../../../oshepherd/api/generate/models.py) | Required model/prompt; options, images, system/template/context/raw/keep_alive; default `stream=false`, `format=json`; worker uses `ollama.generate`; JSON or NDJSON | Same normalized request and response records; stream true/false/omitted; Unicode, multiple records per transport batch, genuine final metadata |
| C02 | [`POST /api/chat`](../../../oshepherd/api/chat/routes.py), [model](../../../oshepherd/api/chat/models.py) | Required model; user/assistant/system roles; default `stream=false`, `format=""`; worker uses `ollama.chat` | Same supported fields/defaults and responses; nullable optional values; validation failures do not enqueue work |
| C03 | [`POST /api/embeddings`](../../../oshepherd/api/embeddings/routes.py), [model](../../../oshepherd/api/embeddings/models.py), [worker](../../../oshepherd/worker/tasks.py) | Accepts `input` string, maps to legacy Ollama `prompt`; returns single `embedding` array | Exact array shape/numeric values; `input` compatibility; options/keep_alive; large JSON through private Storage still returns JSON, not a URL |
| C04 | [`POST /v1/systemone`](../../../oshepherd/api/systemone/routes.py), [model](../../../oshepherd/api/systemone/models.py), [focused tests](../../../tests/test_systemone.py) | State text/object/array; questions dict; preserves extra top-level/question fields; always non-streamed, even with extra `stream=true`; returns answers/usage | Mandatory from the first non-streamed slice; forward raw payload to local `/v1/systemone`; preserve upstream validation responsibility |
| C05 | [`GET /api/tags`](../../../oshepherd/api/tags/routes.py), [registry](../../../oshepherd/api/network_data.py) | Models from live workers, deduplicated by name, response `models` array | No stale/offline models; empty cluster gives empty array; explicit alias/digest policy; only authorized cluster |
| C06 | [`GET /api/ps`](../../../oshepherd/api/ps/routes.py), [registry](../../../oshepherd/api/network_data.py) | Concatenates reported running-model lists across live workers; can be empty | Preserve fields/array; no misleading deduplication that conceals separate workers; metadata freshness measured |
| C07 | [`POST /api/show`](../../../oshepherd/api/show/routes.py), [registry](../../../oshepherd/api/network_data.py) | Required model; accepted `verbose` currently ignored; wraps cached show object under `model_info`; missing model produces 404 with error/message | Preserve initial wrapper/missing behavior; selected metadata agrees with routed digest; treat upstream show-format correction as separate change |
| C08 | [`GET /api/version`](../../../oshepherd/api/version/routes.py), [registry](../../../oshepherd/api/network_data.py) | First live worker version, or `{}` with no live workers | Preserve shape/empty behavior; select a deterministic representative worker; version string alone must not authorize capabilities |
| C09 | [`GET /health`](../../../oshepherd/api/health.py) | Always `{"status":200}` with HTTP 200; liveness only | Same public response; add a separate readiness probe; no inference side effects |

Compatibility concerns in the current baseline must be explicit:

- Chat Pydantic models drop tools and image fields; tool roles and object JSON schemas are not supported by the current schema. Do not count silently dropped fields as successful support. Broader support needs a versioned change and tests across both backends/gateways.
- Generate/chat omitted-stream behavior differs from upstream Ollama's HTTP streaming default. Preserve Oshepherd behavior during backend introduction; publish any later change.
- System One has tested error conventions: invalid top-level input is 422 before queueing; publish/result/upstream failures map to 500 with `error: "Internal Server Error"` and a message. Preserve these for existing failure categories. New platform/admission/auth errors have their own documented statuses.
- Chat/generate/embeddings validation occurs inside request handlers and their error behavior is not comprehensively tested. Capture it in characterization fixtures; do not assume all invalid requests currently yield 422. Fix inconsistent validation deliberately in the shared contract rather than recreate an accidental unhandled exception in Deno.
- Legacy embeddings accepts Oshepherd's `input` and, since the November 15 local
  acceptance slice, the Python SDK's `prompt` alias on both backends and Edge.
  `input` wins when both are supplied; queue payloads remain canonical `input`.
  `/api/embed` remains new functionality outside the current endpoint scope.
- Trailing-slash requests are used in existing e2e tests. Preserve successful client behavior with a method/body-preserving redirect or direct path normalization, including behind the Edge prefix.
- Current FastAPI app does not configure CORS or application-key auth. Browser CORS and Supabase API keys are additions, not claims about existing protection. Restrict configured browser origins; never publish an operator key in a public frontend.

## Operational properties

| ID | Current evidence | Required Supabase behavior / explicit difference |
| --- | --- | --- |
| C10 | [CLI](../../../oshepherd/cli.py), [API config](../../../oshepherd/api/config.py), [worker config](../../../oshepherd/worker/config.py) | Existing commands/env files still select Redis; Supabase worker uses `--backend supabase`; default Supabase gateway is deployed Edge, optional FastAPI uses the same flag on `start-api`; validate unrelated credentials only for their backend |
| C11 | [Celery configuration](../../../oshepherd/worker/app.py): concurrency, prefetch=1 by default, late acknowledgements, requeue on worker loss | Bounded local and SQL-enforced capacity; atomic compatible claim; explicit leases; no exactly-once inference claim; no hidden prefetch; document lack of Celery prefork process/memory-recycling equivalence |
| C12 | [Retry task](../../../oshepherd/worker/ollama_task.py): five retries, backoff capped at 60 s, jitter, connection exceptions | Configurable five retries with deadlines; classified transient failures; idempotent transport retries; no new inference after any stream batch is durable |
| C13 | [Worker state](../../../oshepherd/worker/worker_data.py), [registry](../../../oshepherd/api/network_data.py): nominal 10 s heartbeat, offline after 60 s | Outbound HTTPS-only worker connectivity to Supabase plus local Ollama; independent heartbeat during loading/generation; equivalent freshness; inventory updates separate from liveness |
| C14 | [Celery app](../../../oshepherd/worker/app.py): actual `result_expires=1800`; worker config declares 3600 but app overrides it | Explicit 30-minute unacknowledged terminal-result retention by default; active data cannot expire mid-stream; async retention separate; document actual effective values |
| C15 | [Redis service](../../../oshepherd/common/redis_service.py), [chat route](../../../oshepherd/api/chat/routes.py): live Pub/Sub, enqueue before subscribe | Durable batches/cursors recover missed notifications and late readers; deduplicate; no silent gaps. This improves an existing race rather than preserving it |
| C16 | [Task executor](../../../oshepherd/worker/tasks.py), [worker data](../../../oshepherd/worker/worker_data.py) | Consistent configured Ollama host for inference and metadata; metadata currently mixes explicit base URL and global SDK configuration, so characterize precedence before changing Redis behavior |
| C17 | [API server](../../../oshepherd/api/server.py), [System One tests](../../../tests/test_systemone.py) | FastAPI remains responsive during waits; preserve submit/wait thread affinity for Celery; validate actual multiprocess startup; Edge has per-invocation clients/buffers and no assumed shared process state |
| C18 | Existing routes await inference without an Edge wall-clock limit | Edge accepts finite budgets; non-streamed limit differs from paid streaming limit; persistent FastAPI proves >400 s synchronous execution; Vercel enforces its own deployed duration/body budget; async job outlives all submission invocations |
| C19 | README promises endpoint/client support, not all Celery features or all Ollama fields | Document accepted limits and unsupported configuration/fields; no claim of general Celery replacement, complete Ollama parity, zero-cost hosting, or guaranteed client reconnection |

## Acceptance cases required for implementation

| Gate | Tests and expected invariant |
| --- | --- |
| V01: contract fixtures | Parameterize C01–C09 across existing Redis, Edge/Supabase, and FastAPI/Supabase; use identical stub upstream output. Compare parsed JSON, field defaults, HTTP status/content type, and exact reconstructed NDJSON bytes. Exercise Python and JS clients plus raw HTTP |
| V02: claim races | Many workers claim one job; only one current attempt. Two claim calls for one worker cannot exceed enrolled capacity. Mixed models/clusters cannot claim each other's jobs; offline/draining workers cannot claim |
| V03: retry and acknowledgement loss | Lose responses after successful submit/append/finish commits; replay same operation returns same result without duplicate execution/data/accounting. Conflicting idempotency hash or sequence content is rejected. Retry ceiling and scheduled backoff enforced |
| V04: fencing | Expire a lease, claim a new attempt, then let the old process append/renew/finish. All stale mutations fail. A restarted worker session fences its predecessor; failures cannot overwrite success |
| V05: stream recovery | Drop first/middle/final notifications, duplicate/read pages out of order, reconnect hint transport, miss subscription entirely. Every committed byte is replayed in sequence or the client gets an explicit error; terminal state cannot hide missing output |
| V06: cancellation and cleanup | Client closes stream/non-streamed request; gateway crashes; worker crashes during model loading; consumer/worker leases expire independently; cleanup stops or expires work and releases quotas once. Async jobs survive consumer disappearance |
| V07: deadlines | Cold load, long prompt, long reasoning silence, long output, queue saturation; gateway and worker honor the smaller applicable budget. Test finalization racing cancellation/deadline. No fake success, retry beyond deadline, or new claim of expired work |
| V08: backpressure and sizes | Slow reader, no reader, UTF-8 split, one record larger than a batch, base64 image input, large embeddings, maximum payload/result. Buffers/page sizes stay bounded; source bytes match; oversized cases fail explicitly |
| V09: storage reconciliation | Upload succeeds but finish response is lost; failed/partial upload; stale attempt uploads; signed URL expiry; result expires; cleanup during active read. No success points to incomplete/deleted content; no unauthorized object access; orphan purge works |
| V10: auth isolation | Wrong key, wrong cluster, revoked worker, forged IDs, disallowed RPC/table access, cross-job Storage reads, session rotation, stale Realtime membership. Empty/wrong JWT never falls through to privileged access |
| V11: startup and packaging | Fresh install, Supabase extra missing, config precedence/inherited fields, wrong schema/protocol, Redis absent in Supabase mode, Supabase absent in Redis mode. Both API and worker start independently; no backend import-time connections |
| V12: deployment lifecycle | Fresh migration + upgrade, old/new protocol overlap, worker drain/restart, gateway restart, bounded cleanup, failed migration, rollback to Redis for new jobs. Never double-submit in-flight jobs across backends |
| V13: load and cost | Fixed fixture at 1/5/10/25 active streams, many idle workers, concurrent metadata and non-stream requests; measure candidate latency targets, CPU/memory, DB/WAL size, RPC count, Realtime/Storage/egress, backlog, recovery after saturation |
| V14: real-world smoke | Known real Ollama models/hardware: chat/generate, legacy embeddings, System One, model inventory and missing models; cold/warm runs. Verify end-to-end usability without making sampled text an equality oracle |
| V15: gateway limits | Hosted Free and paid: stream/non-stream, delayed body, idle gaps, sequential reused isolate and overlaps, platform kill, CPU cap, timeout error semantics, CORS/path/key support. Persistent FastAPI >400 s synchronous; Vercel Hobby/Pro deadlines and JSON/NDJSON payload boundaries tested separately; Edge async >400 s with later result retrieval |
| V16: fresh installation | Supabase web flow, CLI fallback, Vercel with existing project, and Marketplace project where supported. Correct schema/policies/Storage/Cron/auth configuration; unauthenticated requests denied; no Redis dependency; infrastructure readiness distinct from connected worker |
| V17: install recovery | Fail after each bootstrap stage; lose successful provider responses; expire OAuth tokens; close browser; run two installers concurrently. Resume without duplicate projects/jobs/owners, changed key secrets or reapplied migrations. CLI and web histories agree; existing unrelated resources/configuration survive |
| V18: installer authority | Forged/replayed OAuth state, wrong project/organization, spoofed Vercel callback, first-visitor ownership claim, leaked enrollment code, expired/reused challenge, revoked grants. No privilege granted; no secrets in URLs/logs/build output/client bundles; installer token deletion leaves runtime working |
| V19: Vercel deployment | Clean template deploy using a released package; correct resolved ASGI entrypoint and configured duration; no Celery/Uvicorn startup; lifespan termination; cold/warm concurrent requests; JSON and streamed responses around 4.5 MB; authenticated client path unaffected by cache or deployment protection. No orphan computation after consumer lease expiry |
| V20: environments and lifecycle | Preview has no production credentials or worker access; redeploy does not migrate; additive upgrade with old worker, failed deploy, gateway rollback and credential rotation. Uninstall disables owned runtime resources without deleting unrelated data or provider projects |
| V21: first inference and support | Operator onboards a browserless worker without admin credentials, SMTP setup or existing Redis; local Ollama/model issues diagnosed; gateway JSON and terminal NDJSON succeed. Separate setup/inference timing; doctor redacts secrets/prompts; paused project/quota/auth failures produce actionable recovery |
| V22: async contract | Follow the handoff's async v1 policy: chat/generate reject explicit streaming; submission disconnect preserves accepted work; unknown/other-caller IDs return 404; pending/result/failure/content-expiry map to 202/200/409/410; oversized successful result remains downloadable; signed download requires current authorization; cancellation/finish race preserves the winning terminal state |

A gateway can enforce a smaller workload envelope, but every supported route must pass its relevant contract and explicit-limit cases. Edge-only runtime tests cannot prove FastAPI behavior; a passing FastAPI test cannot establish hosted Edge support.

## Existing tests: what they prove and miss

- [`tests/test_systemone.py`](../../../tests/test_systemone.py) is the only focused service-free suite. It verifies validation/passthrough, official Python client state types, worker request behavior, error mapping, and Celery thread affinity. Reuse these assertions with backend injection; retain the thread-specific test for the Redis adapter.
- [`tests/test_e2e_api.py`](../../../tests/test_e2e_api.py) uses a hardcoded FastAPI host and live models. Its `test_basic_embeddings_using_ollama` calls **local Ollama directly**, so it does not establish Oshepherd embedding parity. Its raw `/api/show` assertion uses the current nested wrapper, while the SDK assertion expects upstream top-level fields; characterize this inconsistency instead of claiming both pass.
- [`tests/test_e2e_streaming.py`](../../../tests/test_e2e_streaming.py) checks chat/generate output and `done` markers with live models, bounded to 128 output tokens. It does not exercise lost messages, reconnects, long lifetimes, large results or concurrent claims.
- The existing [release workflow](../../../.github/workflows/bump-and-release.yaml) is not a backend compatibility pipeline. Add deliberate CI jobs for deterministic tests, migration/RPC/policy integration, and separately gated hosted/live-model suites. Do not turn credential absence into a silent passing hosted test.

Available deterministic and isolated local commands:

```sh
# Service-free characterization and operator contracts.
uv run --locked pytest -q tests/contracts tests/test_systemone.py

# Real isolated local stack; retains the known strict disconnect failure.
uv run --locked pytest -q tests/integration/supabase -k 'not local_ollama_model' --tb=short

# Planned e2e fixtures read backend URL/key and model availability explicitly.
# Run once for each gateway/backend under test; current tests need refactoring first.
uv run --locked pytest -q tests/test_e2e_api.py tests/test_e2e_streaming.py
```

Do not run the current unqualified e2e command and report Supabase validation; it points to the existing FastAPI setup. Hosted integration needs an isolated project, explicitly configured plan/region, and cleanup scoped to test data.

## Research and verification actually completed

On October 4, 2026:

1. Inspected all route registrations and request models, worker execution/retry/Celery configuration, metadata publishing/aggregation, CLI/config loading, dependencies, README and existing tests. There are nine registered routes.
2. Ran `.venv/bin/pytest -q tests/test_systemone.py`: **13 passed**. No live Redis/Ollama/Supabase system was validated by this run.
3. Used local Pydantic probes to confirm omitted-stream defaults, generate's default JSON format, chat extra-field dropping, and legacy embeddings' required `input`.
4. Verified the installed `ollama==0.6.3` client's Edge path/Bearer-key construction using a mock HTTP transport in the earlier research pass.
5. Used a local HTTP mock with one valid NDJSON chunk and no terminal `done`: the Python client iterator returned normally with `terminal_done=false`. Graceful EOF without a final result can therefore be silent; a real abrupt platform close may behave differently and must be tested.
6. Consulted primary Supabase, Vercel, PostgreSQL, Celery and Ollama sources linked from the implementation and installation plans for runtime/resource limits, authentication, queue semantics, durable-notification tradeoffs and API behavior.
7. Reviewed official Supabase OAuth/Management API/CLI and Vercel FastAPI/Deploy Button/Marketplace documentation for installation feasibility. The Management SQL endpoint is experimental; no button or hosted installer has been implemented or exercised.

That list records the original research baseline. SQL migrations, Deno gateway,
worker adapter and local fault/route tests were subsequently implemented; see the
current ledger. Hosted verification, comprehensive parity, measured load/cost,
soak and OAuth/provider automation remain incomplete. The Edge proxy disconnect
failure prevents a production-readiness claim.
