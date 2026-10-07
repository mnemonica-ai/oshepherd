# Step 0: local characterization evidence

Date: October 4, 2026. Branch: `supabase`. Checkout: `f06d79e`, with runtime
baseline `1ed65b0` unchanged (`git diff 1ed65b0 HEAD -- oshepherd tests
pyproject.toml setup.py requirements.txt` was empty before this slice).

Status: **local characterization complete; step 0 relay/installer experiments
not run**. This is not a Supabase backend, release-readiness or hosting report.
No cloud resource was created, no migration was applied, and no inference ran
against a real model or an existing Redis deployment.

## Artifacts and coverage

- 67 checked-in route cases for C01–C09: 27 normalized inference payloads;
  defaults, explicit null/empty values, supported fields, dropped/preserved
  extras, System One state types, JSON responses, stream bytes, injected
  failures, validation, metadata freshness/deduplication/wrapping and liveness.
- Five Draft 2020-12 request schemas, checked against current Python models and
  validated against positive/negative fixtures. They describe normalized
  payloads; Supabase RPC/Auth/async schemas remain to be finalized.
- Service-free route tests using the real handlers and registry, with a fixed
  registry clock and fake task/result/Redis boundaries. No queue connection is
  needed. All nine trailing-slash paths are checked; POST redirects retain
  their method and payload.
- Worker tests run the actual Celery executor and Ollama SDK against a fake
  ASGI upstream through HTTP mock transports. They check embeddings' legacy
  prompt mapping, System One passthrough, SDK response normalization, exact
  serialized stream records and upstream exceptions.
- A runnable fake Ollama service supports configurable first-body delay, long
  inter-chunk silence, arbitrary byte fragments, graceful incomplete EOF,
  abrupt exceptions, cancellation and large output.
- CI runs only these deterministic contracts plus the existing System One
  baseline. Missing dependencies fail the suite; no hosted skip is disguised
  as a passing integration test.

This covers the **existing Redis behavior portion of V01**. Fake output/fault
scenarios prepare V05/V07/V08/V15; they do not establish database durability,
gateway buffering/deadlines or hosted limits. Two-identity authorization,
claim races, RLS and all Supabase-specific acceptance cases remain unverified.

## Commands and results

From the repository root. Recipes below use the current uv workflow; the dated
results describe the original October 4 run, before the uv migration:

```sh
uv run --locked python --version
uv run --locked pytest -q tests/test_systemone.py
uv run --locked pytest -q tests/contracts tests/test_systemone.py
uv run --locked black --target-version py312 --check tests/contracts tests/support
git diff --check
uv build --out-dir /private/tmp/oshepherd-supabase-characterization
```

- Python: 3.12.3, Anaconda build, macOS arm64.
- Initial baseline: **13 passed in 0.54 s**.
- Final combined suite: **162 passed in 0.83 s** (149 new characterization
  cases plus the existing 13).
- Black: eight files unchanged; diff whitespace check passed.
- Source distribution and a wheel built from the extracted clean source
  distribution passed archive checks: no tests, environment files or worker
  session state; dev/tests dependency metadata matches the source.
- CI configuration is added; GitHub CI has not run from this working tree.

Tested versions are pinned in `requirements.txt`: pytest 9.1.1, FastAPI 0.141.1,
Starlette 1.6.0, Pydantic 2.13.4, Ollama 0.6.3, HTTPX 0.28.1, Celery 5.6.3,
Redis 8.1.0, Uvicorn 0.52.4, Black 26.5.1. JSON Schema validation adds
jsonschema 4.23.0, attrs 26.1.0, jsonschema-specifications 2025.9.1,
referencing 0.37.0 and rpds-py 2026.6.3. The build used setuptools 80.9.0,
also pinned in the requirements. Both package metadata files explicitly
declare HTTPX and the JSON Schema validator in the dev/tests extras.

For this existing venv, pip was absent; the validator was installed with
uv's pip interface to install `jsonschema==4.23.0`. That command
required sandbox approval for uv's cache. The no-isolation build initially
failed because setuptools was absent; installing it with
Installing `setuptools==80.9.0` through uv resolved that. These historical fixes
are now declared in the development dependencies and isolated build backend;
the current setup uses `uv sync --locked` and `uv build`.
Fresh Python venvs/CI can use the handoff's pip setup and the pinned requirements.

Package discovery is restricted to `oshepherd` in both metadata files so the
new test service is excluded from wheels as well as source distributions.
The existing checkout's ignored `build/lib` held stale test files from a prior
build; a direct wheel reused them. The verified wheel was therefore rebuilt
from the extracted source archive in a fresh temporary directory. To reproduce,
extract the generated tarball into a new directory and run the wheel build
against that directory, then inspect archive entries. No existing build files
were deleted, and no artifact was published.

Observed tooling: Deno 1.41.0 / TypeScript 5.3.3, Node 26.5.0, Supabase CLI
2.116.0, Docker CLI 29.8.1. These are inventory only, **not tested or pinned
relay toolchain choices**. `supabase --version` needed sandbox approval because
CLI startup writes local telemetry. No account/project commands were invoked,
and container-daemon compatibility was not tested.

## Output evidence and limits

| Stream fixture | UTF-8 bytes | SHA-256 |
| --- | ---: | --- |
| `chat.upstream.ndjson` | 445 | `fd4d7e85e9ed1f5d68fd53155f770f29e5e2c2cf9a575589b118aea7807ddff8` |
| `chat.legacy.ndjson` | 810 | `dd32a9156d5ad9ec5414919ed432e0860be075a7e27d372f9aa696d44b951684` |
| `generate.upstream.ndjson` | 398 | `ed1ec5a5bf20c603466582897f5fe93fe8571b5fb681b9c907d2386935c7acba` |
| `generate.legacy.ndjson` | 768 | `95638dcbae136794694c6c535f4b18121a31909db22c70d24024d9cfe3fb71f6` |

The large-output test reconstructs **5,242,888 bytes**, including a codepoint
split across fragments. This is a fake-ASGI capability check, not an accepted
gateway maximum or a bounded-memory implementation. No existing Redis limit
was changed and no experimental Supabase limit is selected by this slice.

Delays of 20 s before first body and 200 s between fragments are checked through
an injected sleep function; they are not wall-clock measurements. ASGI events
confirm normal EOF versus exceptions at three output boundaries. The
in-process HTTPX transport buffers response bodies, so first-token latency,
real HTTP disconnect behavior, database I/O, CPU/memory, region/plan and actual
network ingress/egress are **not measured**.

Genuine upstream terminal records and their duration/count metadata survive
SDK/worker/route serialization. With the terminal record omitted, the stock
SDK and existing Celery worker both complete normally; the published record
has `done: false`. This confirms the plan's requirement to detect incomplete
upstream EOF in the new executor, without changing legacy runtime behavior in
the characterization slice.

## Findings assigned to subsequent slices

1. Generate/chat/embeddings/show parse validation inside handlers. Bad input
   and malformed JSON produce plain-text 500s; System One returns structured
   422 before enqueueing. The shared validator must intentionally fix this
   inconsistency, with a documented compatibility change.
2. SDK processing adds null fields to generate/chat results and stream records.
   Snapshotting raw upstream bytes alone would miss current Oshepherd output.
3. A streamed SDK error includes a status-code suffix (`-1` for an error record
   without an HTTP error status). Workers publish it with `done: true` and then
   raise. Error records must not be mistaken for successful completion.
4. Metadata reflects existing scan order: tags deduplicate by name with the
   last value winning, `/api/ps` keeps separate reported copies and version is
   the first live worker's value. Supabase's planned alias/digest and stable
   representative policies still need explicit tests.

## Not run and next work

The submit/claim/append/read/finish spike, SQL concurrency/RLS tests, two
identities, external worker, Edge Free/paid measurements, separate Vercel ASGI
relay, real Ollama smoke, installer provider APIs, CLI migration/deploy and
Marketplace permission tests are **not run**. No hosted credentials, target
projects or cloud budget were assumed. Keep these gates open.

Next: finalize RPC/idempotency/Auth/admission/output/async wire schemas; add an
isolated local SQL/Edge/external-worker spike and select/lock its tested
toolchain. Shared executor/backend extraction can proceed using these fixtures
while hosted access is arranged. Keep spike SQL outside production migrations.

Migration/rollback: no schema, deployment, runtime default or CLI change.
Removing the added test infrastructure restores the previous development
setup. The release workflow still runs for eligible pushes to `main`, including
documentation changes; no merge or release is part of this slice.

Cleanup: tests create temporary scenario JSON through pytest's temporary-path
fixture; no cloud cleanup is required. Ignore/package exclusions now cover
future worker-session files and generated local Supabase state. No real session
file, enrollment credential or model artifact was created.
