# oshepherd

<p align="center">
  <img src="assets/oshepherd_logo.png" alt="oshepherd" width="180">
</p>

<p align="center">
  <a href="https://supabase.com">
    <img src="https://supabase.com/brand-assets/supabase-icon-green-transparent.svg" alt="Supabase" width="80">
  </a>
</p>

Distributed inference for your Ollama servers, using **Supabase as the primary
broker**. Point the official Ollama Python client at one gateway and let
oshepherd route requests to your connected workers. Models and inference stay on
your machines; prompts, jobs and results pass through Supabase.

**Choose a walkthrough:**

| Example | Where Supabase runs | Guide |
| --- | --- | --- |
| Fully local | Docker containers on your machine, including Studio | [Local walkthrough](docs/supabase-walkthrough.md) |
| Supabase Cloud | Hosted project created by `oshepherd share`; Ollama stays on your machine | [Cloud walkthrough](docs/supabase-cloud-walkthrough.md) |

Both examples cover setup, Python requests, database inspection and restart.
Once an installation is running, follow the [worker onboarding walkthrough](docs/supabase-worker-walkthrough.md)
to add an Ollama server, or the [client onboarding walkthrough](docs/supabase-client-walkthrough.md)
to give a script its own credentials.

Supabase support is experimental and published as the `0.1.0rc1` release
candidate: `uv tool install --python 3.12 'oshepherd[supabase]==0.1.0rc1'`. See the
[documentation index](docs/README.md) for installation, operations, database
references and development guides, and the [progress ledger](docs/plans/supabase/progress.md)
for validation status.

## Moving from Redis to Supabase

<p align="center">
  <a href="https://redis.io">
    <img src="https://raw.githubusercontent.com/devicons/devicon/master/icons/redis/redis-original-wordmark.svg" alt="Redis" height="64">
  </a>
  &nbsp;&nbsp;
  <a href="https://docs.celeryq.dev">
    <img src="https://raw.githubusercontent.com/celery/celery/main/docs/images/celery_512.png" alt="Celery" height="64">
  </a>
  &nbsp;&nbsp;
  <a href="https://fastapi.tiangolo.com">
    <img src="https://raw.githubusercontent.com/fastapi/fastapi/master/docs/en/docs/img/logo-teal.svg" alt="FastAPI" height="64">
  </a>
</p>

The original architecture uses a FastAPI gateway, Celery and Redis. Supabase now
provides the main development and onboarding path: Postgres coordinates durable
jobs and worker leases, Auth gives each worker a restricted identity, Storage
holds larger results, and an Edge Function exposes the inference API.

```mermaid
flowchart LR
    C[Ollama Python client] -->|Application key| E[Supabase Edge gateway]
    E <--> Q[Postgres jobs and output]
    W[oshepherd worker] <-->|Outbound requests| Q
    W --> O[Local Ollama]
    W <--> S[Supabase Storage]
    E <--> S
    W --> A[Supabase Auth]
```

Workers make outbound connections to Supabase. A hosted installation does not
need an inbound port or tunnel to Ollama. The same SQL, Auth, Storage and Edge
path can be tested first with local Supabase containers.

| Area | Supabase primary workflow | Redis legacy workflow |
| --- | --- | --- |
| Start serving | `oshepherd share` / prepared local connection | `start-api` and `start-worker` |
| Add workers | `invite`, then `join` with a private one-use invitation | Configure the worker's Redis connection |
| Python client | Gateway URL plus application bearer key | FastAPI URL |
| Run infrastructure | Supabase; Ollama stays local | FastAPI, Celery and Redis |
| Guide | [Local](docs/supabase-walkthrough.md) / [Cloud](docs/supabase-cloud-walkthrough.md) | [Redis setup](docs/setup-redis.md) |

Redis remains supported as a legacy option. Existing `start-api` and
`start-worker` commands retain their Redis defaults; choosing Supabase does not
migrate Redis jobs or switch an existing deployment automatically.

## Try it locally

Follow the [local walkthrough](docs/supabase-walkthrough.md) for Docker setup,
Studio, persistent state and Python requests. It needs Python 3.12, uv, Docker,
Supabase CLI **2.116.0**, `psql` and Ollama. For a hosted database, use the
[cloud walkthrough](docs/supabase-cloud-walkthrough.md); it needs no local
Supabase containers, Supabase CLI or `psql`.

After setup, add [workers](docs/supabase-worker-walkthrough.md) or
[clients](docs/supabase-client-walkthrough.md), and inspect
[worker logs](docs/supabase-worker-walkthrough.md#watch-worker-logs).
Developers can run the separate [disposable acceptance workflow](docs/setup-supabase-local.md),
which removes its own test database when it finishes.

## Use the Ollama Python package

`client.json` contains the gateway URL and your private application key. The URL
includes `/functions/v1/ollama`; it is not the database REST endpoint.
This example uses the manual local walkthrough's state directory. For cloud,
use `.oshepherd/cloud/first-run/client.json`, or use a named client's export.

```python
import json
from pathlib import Path
from ollama import Client

settings = json.loads(Path(
    ".oshepherd/local/end-user/operator/client.json"
).read_text())
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=145,
)

for chunk in client.chat(
    model="llama3.2:3b",
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
    options={"num_predict": 32},
    stream=True,
):
    print(chunk.message.content or "", end="", flush=True)
```

Both `Client` and `AsyncClient` are exercised in the local acceptance workflow.
The supported routes match the existing Redis endpoint set:

| Operation | Route | Python client |
| --- | --- | --- |
| Generate, JSON or streaming | `POST /api/generate` | `generate()` |
| Chat, JSON or streaming | `POST /api/chat` | `chat()` |
| Legacy embeddings | `POST /api/embeddings` | `embeddings()` |
| System One decisions | `POST /v1/systemone` | `systemone()` |
| Available models | `GET /api/tags` | `list()` |
| Running models | `GET /api/ps` | `ps()` |
| Model information | `POST /api/show` | `show()` |
| Version | `GET /api/version` | Authenticated HTTP request |
| Gateway health | `GET /health` | Authenticated HTTP request |

This is the existing oshepherd API contract, not full upstream Ollama parity.
`embed()` (`/api/embed`), model pull/create/delete and additional request fields
are outside this milestone. The existing show response wraps upstream model
information inside `model_info`. Legacy embeddings accepts the Python SDK's
`prompt` as well as `input`.

System One requires a compatible installed decision model. Models advertising
the `decision` capability are registered automatically. The
[acceptance guide](docs/setup-supabase-local.md#endpoint-scope-and-limits)
explains how to include real decision inference in validation.

## Current limits and remaining work

Synchronous requests have a 135-second budget, including queueing and model
loading. Edge request/response bodies are capped at 1 MiB. The asynchronous
`/jobs` API supports longer work and results up to 32 MiB, using a separate client
flow. Closing a streaming client does **not yet reliably cancel** inference
through the Edge proxy; this remains a release qualification gate.

See the [remaining plan](docs/plans/supabase/next-steps.md),
[compatibility matrix](docs/plans/supabase/compatibility.md) and
[progress ledger](docs/plans/supabase/progress.md). Supabase is the primary
workflow, but the candidate is not production-qualified.

## Development

```sh
uv sync --locked --extra supabase --extra installer
uv run --locked pytest -q tests/contracts tests/test_systemone.py
# After changing packaged Python or Edge sources:
uv run --locked python -m scripts.build_supabase_release
uv build
```

Keep SQL migrations immutable. Refresh the release manifest after source changes
before running release-integrity tests. `uv.lock` records dependency versions;
`requirements/runtime.txt` is the locked runtime export used for clean candidate
verification. [Installation](docs/install.md) covers local wheel testing.

The [local integration runbook](docs/plans/supabase/experiments/local-edge-lifecycle.md)
covers the isolated development stack and its strict disconnect test. The
[operator runbook](docs/plans/supabase/experiments/operator-lifecycle.md) covers
upgrade, diagnostics, rotation, revocation and disablement. Redis tests and
startup instructions remain in the [legacy guide](docs/setup-redis.md).

Contributions are welcome through [issues](https://github.com/mnemonica-ai/oshepherd/issues)
and [pull requests](https://github.com/mnemonica-ai/oshepherd/pulls).

Developed by [mnemonica.ai](https://mnemonica.ai). [MIT license](LICENSE).
Supabase's unmodified logo comes from its [official brand assets](https://supabase.com/brand).
The Redis logo comes from [Devicon](https://github.com/devicons/devicon);
the Celery and FastAPI logos come from their official project repositories.
