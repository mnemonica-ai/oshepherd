# Walkthrough: Ollama through Supabase

This guide starts with a clean local Supabase Docker installation and ends with
Python inference through its Edge endpoint. It then connects another Ollama
server to the same installation. Supabase hosts the broker and API; your workers
run the models. No Redis server or PyPI publication is required.

The local flow has previously passed with real models, including System One.
The latest reruns passed the first worker but failed on the joined worker with
Edge CPU-limit responses (503/546). The runner cleans up and exits on failure,
so `--serve` is reached only after all acceptance stages pass. The manual setup
below is available independently. See the [validation ledger](plans/supabase/progress.md#branch-cleanup-follow-up).

Hosted setup
is implemented but the full wizard on a newly created hosted project still needs
acceptance. Stream-disconnect cancellation through Edge remains an open gate.

## 1. Prepare the checkout and models

Use this branch with Python 3.12, [uv](https://docs.astral.sh/uv/), Docker running,
Supabase CLI **2.116.0**, PostgreSQL's `psql` on your PATH, and Ollama installed.
From the repository root:

```sh
uv sync --locked --extra supabase
supabase --version
psql --version
ollama list
```

The examples use `llama3.2:3b` for text and `embeddinggemma:latest` for embeddings.
If missing, download them explicitly:

```sh
ollama pull llama3.2:3b
ollama pull embeddinggemma:latest
```

Start `ollama serve` if Ollama is not already running. Oshepherd discovers
installed models; it does not download them during onboarding.

## 2. Run the complete disposable test

This is the quickest way to check the entire path:

```sh
uv run --locked python -m scripts.supabase_local_acceptance \
  --work-dir "$PWD/.oshepherd/local/walkthrough" --serve
```

The work directory must be new. Reserve ports 56320–56322, 56383 and 11435.
The runner:

1. Starts fresh Supabase containers with an empty database and unique project ID.
2. Runs `oshepherd supabase configure` and `install --create-owner`, then repeats
   installation to verify that cluster identity and keys are preserved.
3. Serves the real Supabase Edge Function and runs `oshepherd share` against
   local Ollama on port 11434.
4. Sends requests with the official Ollama Python package.
5. Starts another Ollama server on port 11435, creates an invitation and runs
   `join`. It stops the first worker and verifies that the joined worker handles
   inference, then repeats after a saved-session restart.
6. With `--serve`, resumes both workers and maintenance for your own requests.

The second Ollama process shares the local model files. This verifies two
servers on one host; it does not establish physical second-machine networking.
The runner reuses an existing default Ollama server and preserves unrelated
containers. Ctrl-C removes its own stack and database. Private logs and
`report.json` remain under the work directory. Omit `--serve` for a test that
cleans up immediately after acceptance.

For a manually managed database that survives the acceptance run, use step 4
instead. The two approaches use the same ports, so stop one before the other.

## 3. Point Python at Supabase

Leave the runner active. From another terminal at the repository root:

```sh
uv run --locked python examples/supabase_client.py \
  --client-file .oshepherd/local/walkthrough/operator/client.json \
  --model llama3.2:3b --embedding-model embeddinggemma:latest
```

A minimal script is:

```python
import json
from pathlib import Path
from ollama import Client

settings = json.loads(Path(
    ".oshepherd/local/walkthrough/operator/client.json"
).read_text())
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=145,
)

print(client.generate(
    model="llama3.2:3b", prompt="Say hello.", options={"num_predict": 32},
).response)

for chunk in client.chat(
    model="llama3.2:3b",
    messages=[{"role": "user", "content": "Explain a queue in one sentence."}],
    stream=True,
    options={"num_predict": 48},
):
    print(chunk.message.content or "", end="", flush=True)

print(len(client.embeddings(
    model="embeddinggemma:latest", prompt="A distributed inference example.",
).embedding))
```

Save it in the checkout and run `uv run --locked python your_script.py`.
Use `AsyncClient` with the same host and headers for async Python calls.

The local gateway is `http://127.0.0.1:56321/functions/v1/ollama`. Do not use
`/rest/v1` as the Ollama client host. The application key authorizes inference;
it is different from a Supabase publishable or administrator key. Keep the
client file private.

The [example client](../examples/supabase_client.py) checks generate/chat JSON
and streaming, legacy embeddings, tags, ps, show, version and health. It can also
check System One. To include real decisions, install a compatible `tev1:4b` and
start a new acceptance run with `--decision-model tev1:4b`; pass the same option
to the example client. Without the flag, the report does not claim System One
inference. Refresh an old model if direct Ollama calls report an unsupported
model format.

## 4. Set up a persistent local installation yourself

Use this alternative when you want to keep the database between sessions.
The following commands create a separate local project with no SQL migrations
preloaded. Run from the repository root after stopping the disposable runner:

```sh
export OSHEPHERD_REPO="$PWD"
export OSHEPHERD_LOCAL="$PWD/.oshepherd/local/manual"
export PATH="$OSHEPHERD_REPO/.venv/bin:$PATH"
umask 077
mkdir -p "$OSHEPHERD_LOCAL/supabase"
python - <<'PY'
import os
from pathlib import Path
import shutil

repo = Path(os.environ["OSHEPHERD_REPO"])
local = Path(os.environ["OSHEPHERD_LOCAL"])
config = (repo / "supabase/config.toml").read_text()
for old, new in (
    ("oshepherd-development", "oshepherd-manual"),
    ("55321", "56321"), ("55322", "56322"),
    ("55320", "56320"), ("55383", "56383"),
):
    config = config.replace(old, new)
(local / "supabase/config.toml").write_text(config)
shutil.copytree(repo / "supabase/functions", local / "supabase/functions")
PY
cd "$OSHEPHERD_LOCAL"
supabase start --exclude realtime,studio,logflare,vector,postgres-meta,supavisor,imgproxy,mailpit
supabase status -o json > status.json
chmod 600 status.json
oshepherd supabase configure --local-from "$OSHEPHERD_LOCAL/status.json" \
  --output "$OSHEPHERD_LOCAL/operator/connection.json"
oshepherd supabase install \
  --connection-file "$OSHEPHERD_LOCAL/operator/connection.json" \
  --state-dir "$OSHEPHERD_LOCAL/operator" --create-owner
```

Use a new `manual` directory for initial setup; don't rerun the copy step over
an existing installation. `--create-owner` creates and journals the local Auth
owner. Installation applies the packaged migrations and produces private
`connection.json`, `edge.env` and `client.json` files. Repeating `install` with
the same state resumes the same installation.

Serve the gateway in this terminal:

```sh
supabase functions serve ollama \
  --env-file "$OSHEPHERD_LOCAL/operator/edge.env" --no-verify-jwt
```

`--no-verify-jwt` disables Supabase's outer JWT check; the function itself still
requires the oshepherd application key. Local internal Supabase connectivity is
injected by the containers.

In another terminal, from the repository root:

```sh
uv run --locked oshepherd share \
  --connection-file "$PWD/.oshepherd/local/manual/operator/connection.json" \
  --state-dir "$PWD/.oshepherd/local/manual/operator" --model llama3.2:3b
```

Wait for **Inference verified**. Keep this process running: it supervises the
worker and stored-result cleanup. Run the Python example with the manual
installation's `operator/client.json`. Ctrl-C stops the worker and cleanup;
repeating the command resumes from saved state.

To stop the containers, run `supabase stop` from the manual project directory.
The CLI normally saves local data for a later `supabase start`; do not use
`--no-backup` if you intend to retain it. Keep the operator state together with
that database. Stopping containers does not rotate or revoke credentials.

## 5. Onboard another Ollama server

For the manual installation above, create a private one-use invitation:

```sh
uv run --locked oshepherd invite \
  --connection-file "$PWD/.oshepherd/local/manual/operator/connection.json" \
  --state-dir "$PWD/.oshepherd/local/manual/operator" \
  --name second-server \
  --output "$PWD/.oshepherd/local/second-server.invitation.json"
```

For another server on the same machine, start it in a separate terminal:

```sh
OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

Then join it, using a separate worker state directory:

```sh
uv run --locked oshepherd join \
  --invitation-file "$PWD/.oshepherd/local/second-server.invitation.json" \
  --state-dir "$PWD/.oshepherd/local/second-worker" \
  --ollama-url http://127.0.0.1:11435
```

To restart later, leave that Ollama server running and omit the invitation:

```sh
uv run --locked oshepherd join \
  --state-dir "$PWD/.oshepherd/local/second-worker"
```

The saved configuration remembers the Ollama URL. Keep `share` running for
maintenance even when another worker handles inference. An expired or uncertain
invitation can be replaced explicitly with `invite --renew` and a new output
path. Joined workers receive restricted Auth sessions, not operator credentials.

**A physical second machine needs a reachable Supabase origin.** The local
configuration above uses loopback: transferring its invitation to another host
will not work. Do not rewrite invitation URLs to bypass project binding. Use a
hosted HTTPS installation for that path; cross-machine self-hosted networking is
not covered by this local acceptance workflow.

## 6. Use a hosted Supabase installation when ready

With the candidate installed and Ollama running, `oshepherd share` without a
connection file opens the hosted setup wizard. It creates or selects a Supabase
project, applies the schema through the Management API, deploys Edge and enrolls
the first worker. It requests the personal access token privately; no database
URL or provider CLI is required for that wizard.

Follow the [hosted Supabase guide](setup-supabase.md) for project selection,
private invitations to another machine, background services and disablement.
For services, install a verified local wheel as described in
[installation](install.md#install-the-0025a1-candidate); editable checkout installs
are sufficient for foreground local testing. This does not require publication.
The latest full local run does not qualify fresh hosted provisioning.

## Limits and troubleshooting

- **Authentication fails:** use the application key from this installation's
  `client.json`, and include `/functions/v1/ollama` in the host.
- **No eligible worker/model:** check Ollama is running, the model is installed,
  and `share` or `join` is still active. Test the model directly with Ollama first.
- **Port or directory already in use:** stop the previous owned test run and use
  a new work directory. The harness will not overwrite an existing run.
- **Slow model:** queueing, loading and inference must fit the 135-second sync
  budget. Increasing the Python timeout does not extend the server budget.
- **Large results:** Edge request/response bodies are limited to 1 MiB. `/jobs`
  supports asynchronous results up to 32 MiB through a separate HTTP workflow.
- **HTTP 503 or 546:** inspect the private Edge log. The latest standard and
  System One acceptance reruns hit the runtime CPU limit on the joined worker;
  increasing the client timeout cannot fix a supervisor limit. Keep that failure
  separate from earlier successful model checks.
- **Stream closed:** cancellation is not reliable through the Edge proxy yet;
  inference may continue. This remains experimental.

Use `embeddings()`, not `embed()`; `/api/embed` and model-management endpoints are
not implemented. See the [supported routes](../README.md#use-the-ollama-python-package),
[local acceptance details](setup-supabase-local.md), and
[remaining plan](plans/supabase/next-steps.md).
