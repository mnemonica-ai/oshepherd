# Test distributed inference with local Supabase

For interactive local use with Supabase Studio enabled by default, follow the
[single-worker walkthrough](supabase-walkthrough.md). The automated acceptance
runner below uses a minimal disposable stack without Studio.

This workflow uses a new Docker Supabase project, the real Edge gateway,
`oshepherd` CLI commands and the official Ollama Python package. No hosted project,
PyPI publication or Redis server is needed. The existing development stack is
preserved. This is a local functional test; hosted qualification and the known
stream-disconnect cancellation failure remain separate. Earlier full runs passed,
but the latest branch-cleanup reruns hit Edge CPU limits on the joined worker
(HTTP 503/546), exited and cleaned up. See the
[validation ledger](plans/supabase/progress.md#branch-cleanup-follow-up).

## Requirements

- Docker running, uv, PostgreSQL's `psql`, and Supabase CLI **2.116.0**.
- Ollama installed. If its default server is already running, it is reused;
  otherwise the harness starts and later stops its own server.
- Installed models: `llama3.2:3b` and `embeddinggemma:latest`, or explicitly chosen
  replacements. Models are never downloaded automatically.
- Available ports 56320–56322, 56383 and 11435. The second Ollama server uses
  11435 and the same local model files, with a separate server and worker process.

From the checkout:

```sh
uv sync --locked --extra supabase
uv run --locked python -m scripts.supabase_local_acceptance \
  --work-dir "$PWD/.oshepherd/local/my-first-acceptance"
```

The work directory must not already exist. Each run creates a uniquely named
Supabase project with an empty database. It copies only the gateway sources into
that project; all Oshepherd migrations are applied by `oshepherd supabase install`.
It imports Supabase status into private operator configuration, creates a private
owner, verifies repeat installation, serves Edge, runs `share`, and executes
[the Python client](../examples/supabase_client.py).

It then uses `invite` and `join` to connect a second Ollama server. The first worker
is stopped before repeating the client checks, and SQL confirms completed jobs
belong to the joined worker. The joined worker restarts from its saved session and
the checks run again. Joined state is checked for operator credentials.

The runner stops its processes and removes only its own test stack and volumes.
Private logs and `report.json` stay in the work directory. A pre-existing Ollama
server and unrelated Docker containers are preserved. Runs currently use the
checkout's editable installation; they are not published-package acceptance.

## Make your own requests

Add `--serve` to the runner to leave both workers, cleanup and Edge running after
acceptance. In another terminal:

```sh
uv run --locked python examples/supabase_client.py \
  --client-file .oshepherd/local/my-first-acceptance/operator/client.json \
  --model llama3.2:3b --embedding-model embeddinggemma:latest
```

For a minimal request:

```python
import json
from pathlib import Path
from ollama import Client

settings = json.loads(Path(
    ".oshepherd/local/my-first-acceptance/operator/client.json"
).read_text())
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=145,
)
response = client.chat(
    model="llama3.2:3b",
    messages=[{"role": "user", "content": "Say hello."}],
    options={"num_predict": 32},
)
print(response.message.content)
```

The URL is `http://127.0.0.1:56321/functions/v1/ollama`. Requests travel through
Edge, Postgres and the outbound worker before reaching Ollama. Ctrl-C in the
runner terminal cleans up its test stack, including the database; this runner
is intentionally disposable.

To give a local script a separate bearer key, run these commands while the
stack is up. Each reads `connection.json` from the selected operator directory.

```sh
uv run --locked oshepherd client create \
  --state-dir .oshepherd/local/my-first-acceptance/operator \
  --name local-script --output local-client.json
uv run --locked oshepherd client list \
  --state-dir .oshepherd/local/my-first-acceptance/operator
```

Point the request example at `local-client.json` and send a request. Then revoke
the key and verify that another request with it is rejected.

```sh
uv run --locked oshepherd client revoke \
  --state-dir .oshepherd/local/my-first-acceptance/operator \
  --name local-script
```

The file still uses the loopback gateway on the stack host. A remote consumer
needs a gateway it can reach, such as the hosted HTTPS gateway in the
[cloud walkthrough](supabase-cloud-walkthrough.md). Use `invite` and `join` to
add a worker that serves inference.

## CLI steps exercised by the runner

After starting a fresh local Supabase project and saving `supabase status -o json`
to an owned mode-0600 file, the underlying setup commands are:

```sh
oshepherd supabase configure --local-from /private/status.json \
  --output /private/operator/connection.json
oshepherd supabase install --connection-file /private/operator/connection.json \
  --state-dir /private/operator --create-owner
# From the chosen local Supabase project directory, in a separate terminal:
supabase functions serve ollama --env-file /private/operator/edge.env --no-verify-jwt
oshepherd share --connection-file /private/operator/connection.json \
  --state-dir /private/operator --model llama3.2:3b
```

`--create-owner` replaces manual Auth UUID entry and resumes its journaled owner
on repeat installation. Local status imports bind the selected API/database ports
to loopback and use the selected stack's local administrator for migrations.
This validates real SQL/Auth/Storage behavior, not the hosted Management API or
the privileges of a newly created hosted `postgres` role.

To onboard another server, while the installation is running:

```sh
oshepherd invite --connection-file /private/operator/connection.json \
  --state-dir /private/operator --name another-server \
  --output /private/another-server.invitation.json
oshepherd join --invitation-file /private/another-server.invitation.json \
  --state-dir /private/another-worker --ollama-url http://127.0.0.1:11435
# Later, with the same Ollama server running:
oshepherd join --state-dir /private/another-worker
```

These commands use one-use private invitations without administrator credentials.
Separate state directories are required for multiple workers on one machine.
This test uses two servers on the same host. A physical second machine needs a
reachable Supabase origin: the loopback invitation in this test does not provide
LAN access. Hosted HTTPS onboarding is already a separate `share`/`join` path;
cross-machine local networking is not demonstrated by this test.

## Endpoint scope and limits

The scope matches the existing Redis backend: generate, chat, legacy embeddings,
System One, tags, ps, show, version and health. Generate/chat cover JSON and NDJSON,
and both `Client` and `AsyncClient` are exercised. Legacy embeddings accepts both
the SDK's `prompt` and existing `input`; `input` takes precedence when both exist.
The canonical queue payload still uses `input`. The existing show response wraps
upstream information in `model_info` (`ShowResponse.modelinfo` in the Python SDK).

System One is automatically advertised for models with the `decision` capability.
Add `--decision-model <installed-compatible-model>` to exercise real decision
inference. Real `tev1:4b` inference passed on both local servers and after joined
worker restart following an explicitly authorized model refresh on November 15:

```sh
uv run --locked python -m scripts.supabase_local_acceptance \
  --work-dir "$PWD/.oshepherd/local/my-systemone-test" \
  --decision-model tev1:4b
```

A later Supabase-focused branch rerun hit the Edge runtime CPU limit during
System One on the joined server and returned 503; the first-worker check passed
and cleanup succeeded. The earlier successful runs do not qualify the full
resource envelope. See the [ledger](plans/supabase/progress.md#branch-cleanup-follow-up).

Without that flag, the report's check list does not claim real System One
inference. The separate SDK integration test exercises that route through real
Edge/Auth/SQL with deterministic upstream responses:

```sh
uv run --locked pytest -q tests/integration/supabase/test_edge.py \
  -k 'python_sdk_existing_routes or all_routes' --tb=short
```

That integration command uses the existing 55321 development stack and requires
its normal setup. Run it separately from fresh-stack acceptance to reduce Docker
resource contention.

`/api/embed`, model pull/create/delete, additional chat fields and complete upstream
Ollama parity are outside this milestone. Existing 135-second synchronous and
1-MiB Edge request/response limits still apply. Closing a stream does not yet
reliably cancel its inference through the Edge proxy.
