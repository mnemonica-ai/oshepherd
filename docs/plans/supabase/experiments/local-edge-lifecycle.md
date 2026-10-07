# Local Edge lifecycle experiment

Updated October 6, 2026. Experimental checkout harness; production migrations and the
released installer remain pending. The [progress ledger](../progress.md) owns
the remaining gates.

## Verified lifecycle

HTTP requests reached the real local Supabase Edge runtime, admitted jobs through
PostgREST, and received durable responses from a Python worker outside its
container. The fake upstream runs over a real loopback HTTP socket here. Four
inference paths, both NDJSON paths, metadata, health, application-key rejection,
async admission/replay/status/result and other-caller isolation passed. Async
status matches the versioned JSON schema.

The real-model test used existing Ollama 0.35.0 at `127.0.0.1:11434`, with
installed `llama3.2:3b`, digest
`a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72`.
It requested at most eight tokens, with a 512-token context and temperature zero.
The client received `done: true`, `eval_count: 6`, and 626 UTF-8 response bytes.
The disposable test took 9.46 seconds including setup/cleanup; this is not an
inference latency or hosted performance benchmark.

Versions: Python 3.12.3, HTTPX 0.28.1, Ollama SDK 0.6.3, Supabase CLI 2.116.0,
Edge image `ghcr.io/supabase/edge-runtime:v1.74.3`, Deno contract runner 1.41.0,
Docker 29.8.1, local PostgreSQL 15.8.1.085. Standalone Deno tests alone do not
establish runtime parity.

On October 6 the project switched to uv 0.9.18, a project lockfile and
uv-managed Python 3.12.12. The original dependency versions were preserved.
The real-model Edge lifecycle passed again through `uv run --locked`, in 13.03
seconds including disposable setup/cleanup. Earlier timings and Python 3.12.3
above describe the original runs.

## Repeat from the checkout

Docker, the pinned Supabase CLI and uv must be available. Sync the locked Python
3.12 development environment first. Start Ollama with an already installed model;
nothing downloads one.

```sh
uv sync --locked --extra supabase
uv run --locked python -m scripts.supabase_local start
uv run --locked python -m scripts.supabase_local apply-spike
uv run --locked pytest -q tests/contracts tests/test_systemone.py
deno test --allow-read=protocol supabase/functions/ollama/gateway_test.ts
uv run --locked pytest -q tests/integration/supabase --ignore=tests/integration/supabase/test_edge.py
uv run --locked pytest -q tests/integration/supabase/test_edge.py -k all_routes --tb=short
OSHEPHERD_REAL_OLLAMA_MODEL=llama3.2:3b uv run --locked pytest -q tests/integration/supabase/test_edge.py -k local_ollama_model -s --tb=short
```

The helper accepts only the `oshepherd-development` project at ports 55321/55322.
The socket fake upstream uses 55324. Tests provision temporary confirmed Auth
users, worker membership/capacity and independent application keys. The worker
receives only a publishable key and its JWT; the gateway receives the privileged
project key through the local runtime. State/logs stay in ignored, private
`.oshepherd/local/` files. A file lock serializes Edge launches. Tests terminate
only their process group and delete UUID-scoped SQL/Auth rows. Supabase remains
running; other projects/containers are untouched.

The real request has a 135-second SQL/Edge budget and 140-second client timeout.
Sampled prose is not an equality oracle: assertions check genuine completion,
token limits and schema fields.

## Persistent manual testing

The isolated stack and a persistent demo installation are already prepared on
the development machine. Start the gateway, external worker and independent
maintenance runner from the repo:

```sh
uv run --locked python -m scripts.supabase_demo serve
```

Wait for `Local gateway ready`, then use another terminal:

```sh
uv run --locked python -m scripts.supabase_demo doctor
uv run --locked python -m scripts.supabase_demo request
uv run --locked python -m scripts.supabase_demo request --stream
uv run --locked python -m scripts.supabase_demo request --prompt 'Reply with JSON {"ready":true}.'
```

The gateway is `http://127.0.0.1:55321/functions/v1/ollama`. Doctor verifies local
Ollama/model availability, the SQL protocol, application-key authentication,
gateway health and fresh worker inventory. Request explicitly runs bounded
eight-token inference with a 512-token context and temperature zero. It checks
genuine completion; streaming prints exact NDJSON. The legacy generate default
is `format=json`; an eight-token limit can stop generated text with
`done_reason=length`. Model text is not a golden equality check.

Ctrl-C in the serve terminal stops only the demo's Edge, worker and maintenance processes,
including a bounded worker drain. It preserves Supabase, Ollama, configuration
and results so they can be tested again. Stop the demo before Edge integration
tests: all checkout Edge launches share a lock because one local function name
cannot serve different clusters simultaneously.

From a fresh checkout or after starting the isolated stack, prepare it with:

```sh
uv sync --locked --extra supabase
uv run --locked python -m scripts.supabase_local start
uv run --locked python -m scripts.supabase_local apply-spike
uv run --locked python -m scripts.supabase_demo setup --model llama3.2:3b
```

Setup accepts only this local profile and an already installed generation model.
It creates operator/worker Auth identities, SQL membership/capacity, an application
key and worker session. Repeating `setup` without `--model` resumes the same
installation and preserves its key/session. Persisted operation IDs recover
ambiguous SQL acknowledgement; Auth creation is reconciled by the installation's
randomly named identity before creating another user. An ambiguous single-use
Auth exchange requires explicit `setup --recover-enrollment` to issue a new code.
Public signup and SMTP are unnecessary. No model is downloaded.

All demo files are ignored under `.oshepherd/local/demo/`, mode 0600 inside a
0700 directory. `worker.env` contains only the publishable project key and worker
settings; `worker-session.json` contains the worker's own Auth tokens. `edge.env`
contains cluster/schema/public-origin settings, while the local Edge runtime
injects its privileged project key. `client.json` contains the application key,
gateway URL and model. Logs remain private. No command prints project keys or
Auth tokens. Setup never puts an administrator key in worker files.

Ordinary Ollama SDK clients can use `client.json` without exposing its key in
shell arguments:

```python
import json
from pathlib import Path
from ollama import Client

settings = json.loads(Path(".oshepherd/local/demo/client.json").read_text())
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=140,
)
result = client.generate(
    model=settings["model"],
    prompt='Reply with JSON {"ready":true}.',
    stream=False,
    options={"num_predict": 8, "num_ctx": 512, "temperature": 0},
)
print(result.response)
```

Repeated setup, CLI startup/restart, graceful shutdown, JSON completion and
nine real-model NDJSON records ending with `done: true` passed against the
persistent installation. The supervisor was stopped after verification;
operators start it explicitly with the command above.

The same JSON and nine-record NDJSON checks passed with maintenance running on
October 6. Durable cleanup, orphan protection and optional database scheduling
are covered in [the maintenance report](local-maintenance.md). Cron is a separate
operator step; the demo supervisor supplies the Storage HTTP cleanup runner.

## Open disconnect gate

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py -k first_record --tb=short
```

This retains a strict failing assertion. The first record arrived in under 1.5
seconds while upstream delayed later records by two seconds. After the HTTP
reader closed, the job succeeded rather than being cancelled. First-record
delivery passed; end-to-end disconnect cancellation did not. Direct Web Streams
cancellation and SQL worker cancellation passed separately.

The gateway registers Request abort, stream cancel, reader-stall and execution
deadline hooks. Its entry point also uses response-completion rejection when
available; that did not fix this local proxy observation. The
[Deno server API](https://docs.deno.com/api/deno/http-server/) documents completion,
and [Supabase's architecture](https://github.com/supabase/edge-runtime) includes
a main runtime proxy before the user runtime. The cause within that proxy remains
unconfirmed. Do not advertise disconnect cancellation as verified or mark the
streaming matrix complete.

Output/backpressure/load tests, extended process/refresh recovery, production
migrations, installer and hosted measurements remain open.

## Optional FastAPI follow-up

`start-api --backend supabase` and `OSHEPHERD_BACKEND=supabase` select the new
FastAPI gateway; Redis remains the default and an explicit flag takes precedence.
It uses server-only `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_SCHEMA` and
`OSHEPHERD_CLUSTER_ID`. Configuration requires no Redis/Celery variables. All
nine routes, application-key authentication, exact NDJSON, async status/results
and authorized signed downloads passed through real local Auth/SQL/worker/Storage.

The two FastAPI integration tests passed in 9.26 seconds:

```sh
uv run --locked pytest -q tests/integration/supabase -k fastapi --tb=short
```

The socket test runs a real Uvicorn gateway and socket fake upstream at
55326/55325. After the first record, an actual HTTP reader close cancels the SQL
job and stops the worker read within the test's three-second bound. This verifies
the optional native gateway; it does not repair the separate Edge proxy gate.

The API factory acquires no network resources on construction. HTTP clients and
cursor readers belong to each request/stream and close after response or
disconnect. No permanent collector, migration, reaper or essential post-response
task runs inside the gateway. Authenticated responses use private/no-store caching.
`create_vercel_app()` adds explicit backend/profile and preview-default-denial
checks without Uvicorn or signal setup. The released deployment template and
hosted Vercel measurements remain pending.

Both profiles currently honor the experimental SQL maximum of 135 seconds;
larger planned persistent/Vercel budgets are not implemented or advertised.
The JSON/stream response cap defaults to 1 MiB; Vercel configuration cannot
exceed 4,000,000 bytes. Async successful content can still use private downloads.

## Private result follow-up

Storage subsequently passed two real authorization/commit tests and two real
Edge route/large-result tests. The latter took 12.73 seconds including cleanup.
A successful JSON output with a 1,200,000-character response stayed `succeeded`
when ordinary retrieval returned 413. Explicit signed download returned exact
bytes and SHA-256; another application key received 404. Status/download URLs
use the public project origin rather than the container origin.

Uploads are scoped to current non-streaming attempts. SQL uses a foreign key to
immutable Storage bucket/path metadata, so missing objects cannot become
successful results. The restricted owner receives no Storage SELECT grant.
The sole private-schema EXECUTE grant to authenticated users is a boolean
Storage policy predicate; direct application table grants stay revoked.
Async workers store small results as objects too, supporting explicit download.
Gateway JSON retrieval is capped at 1 MiB; larger async success uses download.

Scheduled cleanup and orphan reconciliation remain pending. Tests remove only
objects beneath their generated cluster UUID after releasing SQL references,
including uncommitted upload orphans.
