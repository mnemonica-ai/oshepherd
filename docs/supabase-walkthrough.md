# Local walkthrough: Ollama through Supabase containers

[Run one Ollama worker locally](#2-create-the-local-supabase-project) to test
inference from Python and inspect its database in Supabase Studio. Docker runs
Supabase, and Ollama runs the models on your machine.

This example runs the gateway, database, Auth and Storage in local Docker
containers, with Studio enabled. Ollama and the oshepherd worker run on the host.
No Supabase account or personal access token is needed. Initial dependency,
container-image and model downloads need internet access.

For a hosted installation, use the separate
[Supabase Cloud walkthrough](supabase-cloud-walkthrough.md).

This guide uses the unpublished `0.0.25a1` candidate. The checkout is enough
for local testing. See [candidate installation](install.md#install-the-0025a1-candidate)
for installation from a local wheel.

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

Check the text model directly before continuing:

```sh
ollama run llama3.2:3b "Say hello in one sentence."
```

## 2. Create the local Supabase project

These commands create the `oshepherd-end-user` project under
`.oshepherd/local/end-user`. Run them once from the repository root. For an
existing installation, [resume the local project](#resume-or-stop-a-local-session).
If you used `.oshepherd/local/manual` with the earlier guide, keep using that
directory in the commands below.

Stop any disposable acceptance run before starting this project. Both use
ports 56320–56322 and 56383. This manual setup also starts Supabase Studio
on port 56323, which must be free.

```sh
export OSHEPHERD_REPO="$PWD"
export OSHEPHERD_LOCAL="$PWD/.oshepherd/local/end-user"
export PATH="$OSHEPHERD_REPO/.venv/bin:$PATH"
umask 077
python - <<'PY'
import os
from pathlib import Path
import shutil

repo = Path(os.environ["OSHEPHERD_REPO"])
local = Path(os.environ["OSHEPHERD_LOCAL"])
config = (repo / "supabase/config.toml").read_text()
for old, new in (
    ("oshepherd-development", "oshepherd-end-user"),
    ("55321", "56321"), ("55322", "56322"),
    ("55320", "56320"), ("55383", "56383"),
):
    config = config.replace(old, new)
config = config.replace(
    "[studio]\nenabled = false",
    "[studio]\nenabled = true\nport = 56323",
)
(local / "supabase").mkdir(parents=True)
(local / "supabase/config.toml").write_text(config)
shutil.copytree(repo / "supabase/functions", local / "supabase/functions")
PY
cd "$OSHEPHERD_LOCAL"
supabase start --exclude realtime,logflare,vector,supavisor,imgproxy,mailpit
supabase status -o json > status.json
chmod 600 status.json
oshepherd supabase configure --local-from "$OSHEPHERD_LOCAL/status.json" \
  --output "$OSHEPHERD_LOCAL/operator/connection.json"
oshepherd supabase install \
  --connection-file "$OSHEPHERD_LOCAL/operator/connection.json" \
  --state-dir "$OSHEPHERD_LOCAL/operator" --create-owner
```

`--create-owner` creates and journals the local Auth owner. Installation applies
the packaged migrations and prepares private `connection.json`, `edge.env` and
`client.json` files. Repeating `install` with the same state resumes the same
installation.

## 3. Start the gateway and worker

From the repository root, enter the local project directory and serve the
gateway. If you are still in that directory from step 2, skip `cd`.

```sh
cd .oshepherd/local/end-user
supabase functions serve ollama \
  --env-file operator/edge.env --no-verify-jwt
```

`--no-verify-jwt` disables Supabase's outer JWT check. The gateway requires the
oshepherd application key for inference.

In another terminal, from the repository root:

```sh
uv run --locked oshepherd share \
  --connection-file "$PWD/.oshepherd/local/end-user/operator/connection.json" \
  --state-dir "$PWD/.oshepherd/local/end-user/operator" --model llama3.2:3b
```

Wait for **Inference verified** and keep `share` running while you send requests.
Ctrl-C stops the worker and result cleanup. Rerun the same command to resume from
saved state.

## 4. Point Python at Supabase

With the gateway and worker running, open a third terminal at the repository root.

```sh
uv run --locked python examples/supabase_client.py \
  --client-file .oshepherd/local/end-user/operator/client.json \
  --model llama3.2:3b --embedding-model embeddinggemma:latest
```

A minimal script is:

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

The [example client](../examples/supabase_client.py) checks generate and chat
in JSON and streaming modes, legacy embeddings, tags, ps, show, version and
health. To check System One, install a compatible `tev1:4b` and pass
`--decision-model tev1:4b` to the client. For an unsupported model-format error
in direct Ollama calls, refresh the model.

### Give a local script a separate key

The [client onboarding walkthrough](supabase-client-walkthrough.md) covers
credential transfer, Python integration, recovery and revocation in detail.

From the repository root, use the operator directory from step 2. The commands
read its private `connection.json`.

```sh
uv run --locked oshepherd client create \
  --state-dir .oshepherd/local/end-user/operator \
  --name local-script --output local-client.json
uv run --locked oshepherd client list --state-dir .oshepherd/local/end-user/operator
```

Set `--client-file local-client.json` in the example above, or update your
script to read that file, then send a request. Keep the file at mode `0600`.
The exported URL points to the loopback gateway on this host. To send requests
from a different machine, use a reachable gateway such as the hosted HTTPS
endpoint in the [cloud walkthrough](supabase-cloud-walkthrough.md).

Revoke the script's key, then verify that a new request with it is rejected.

```sh
uv run --locked oshepherd client revoke \
  --state-dir .oshepherd/local/end-user/operator --name local-script
```

Use `invite` and `join` in step 6 to add a worker that serves inference.

## 5. Inspect the database in Supabase Studio

For local execution activity, watch the `share` terminal or run
`tail -F .oshepherd/local/end-user/operator/local.worker-session.log` in another
terminal. See [worker logs](supabase-worker-walkthrough.md#watch-worker-logs) for
the events and file retention policy.

Step 2 enables Studio and its Postgres metadata service by default. Open
[Supabase Studio](http://127.0.0.1:56323) and select the `oshepherd` schema
in **Table Editor**. Look at `jobs`, `workers`, `worker_models` or `chunks`.
The `oshepherd_api` schema exposes RPC functions. Send another Python request
from step 4 and refresh the tables to inspect its job and output.

### Existing installations created without Studio

If you followed an earlier version of this guide, edit the `[studio]` section of
`.oshepherd/local/end-user/supabase/config.toml`:

```toml
[studio]
enabled = true
port = 56323
```

Stop `share` and `supabase functions serve` in their terminals with Ctrl-C.
Then restart the local project from the repository root:

```sh
cd .oshepherd/local/end-user
supabase stop
supabase start --exclude realtime,logflare,vector,supavisor,imgproxy,mailpit
```

Plain `supabase stop` preserves the database for the next start. The start
command includes Studio and Postgres Meta. Restart the gateway and `share`
using the existing operator files from step 3.
See the [Supabase CLI config reference](https://supabase.com/docs/guides/local-development/cli/config).

### Resume or stop a local session

To resume an existing project, skip the copy, configure and install commands.
If the containers are stopped, enter the local project directory and rerun
the `supabase start` command from step 2. Use the saved operator files when you
start the gateway and worker in step 3. Repeat the Python request to verify
inference after restart.

To end a local session, stop the gateway and worker and run `supabase stop`
from the local project directory. Keep its `operator` directory with the
database so you can resume the same installation.

## 6. Onboard another Ollama server

See the [worker onboarding walkthrough](supabase-worker-walkthrough.md) for the
full invite/join flow, verification, restarts and a remote-machine cloud example.

For the manual installation above, create a private one-use invitation:

```sh
uv run --locked oshepherd invite \
  --connection-file "$PWD/.oshepherd/local/end-user/operator/connection.json" \
  --state-dir "$PWD/.oshepherd/local/end-user/operator" \
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
hosted HTTPS installation from the [cloud walkthrough](supabase-cloud-walkthrough.md)
for that path; cross-machine self-hosted networking is
not covered by this local acceptance workflow.

## Run the full disposable acceptance test

Use the runner to exercise first-worker inference, joined-worker inference and
a saved-session restart in a fresh project.

```sh
uv run --locked python -m scripts.supabase_local_acceptance \
  --work-dir "$PWD/.oshepherd/local/walkthrough" --serve
```

Use a new work directory and reserve ports 56320–56322, 56383 and 11435.
Stop the manual local project before starting the runner.

The runner exercises the following steps.

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

The second Ollama process uses the same local model files and runs on port
11435. The runner reuses an existing default Ollama server and leaves unrelated
containers running. Ctrl-C removes the test stack and its database. Private logs
and `report.json` remain under the work directory. Omit `--serve` for cleanup
immediately after acceptance.

Earlier full local runs passed with real models, including System One. The
latest reruns passed the first worker and hit the Edge CPU limit on the joined
worker (HTTP 503/546). The runner cleans up and exits on failure. It reaches
`--serve` only after all acceptance stages pass. For a sustained first-worker
test, use the [manual setup](#2-create-the-local-supabase-project). See the
[validation ledger](plans/supabase/progress.md#branch-cleanup-follow-up).

Pass `--decision-model tev1:4b` to include System One in a new acceptance run
with a compatible model already installed. The report lists the checks run.

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
