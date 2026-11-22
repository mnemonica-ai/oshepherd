# Worker walkthrough: add another Ollama server

Add a second machine that serves inference through your existing Supabase
installation. The operator creates a private, one-use invitation with `invite`;
the new machine exchanges it for its own restricted worker session with `join`.
It does not need your Supabase account token or operator connection file.

Applications that only send inference requests use the separate
[client walkthrough](supabase-client-walkthrough.md).

## 1. Prepare the two machines

Complete the [cloud walkthrough](supabase-cloud-walkthrough.md) first. These
examples use its operator state directory, `.oshepherd/cloud/first-run`. Keep
`oshepherd share` running on that machine: it runs the first worker and stored
result cleanup. Run operator commands below from that checkout in another terminal.

On the second machine, install Ollama and the same oshepherd candidate as the
operator. Use a matching checkout with Python 3.12 and uv, or follow [candidate installation](install.md#install-the-010rc1-candidate).
For a checkout, run:

```sh
uv sync --locked --extra supabase
uv run --locked oshepherd join --help
```

Keep the Ollama app running, or start `ollama serve` in a separate terminal.
Download a model if needed and verify it directly:

```sh
ollama pull llama3.2:3b
ollama run llama3.2:3b "Say hello in one sentence."
```

The new worker needs outbound access to the hosted Supabase project. Ollama can
stay bound to `127.0.0.1`; no inbound port or tunnel is required.

## 2. Create an invitation on the operator machine

```sh
uv run --locked oshepherd invite \
  --state-dir .oshepherd/cloud/first-run \
  --name second-worker \
  --output .oshepherd/cloud/first-run/second-worker.invitation.json
```

Use the state directory of your existing installation, including its private
`connection.json`. If you used the default `share` directory, omit `--state-dir`.
Choose a different worker name for each additional machine.

The output file is private and must not already exist. Transfer only that file
over a private channel such as SSH. For example, replacing `user@worker-host`
with your SSH destination:

```sh
scp .oshepherd/cloud/first-run/second-worker.invitation.json \
  user@worker-host:~/second-worker.invitation.json
```

The invitation expires and can be consumed once. Anyone holding it can enroll
that worker, so keep it out of source control and messages.

## 3. Join on the second machine

From the matching checkout on the second machine:

```sh
chmod 600 ~/second-worker.invitation.json
uv run --locked oshepherd join \
  --invitation-file ~/second-worker.invitation.json \
  --state-dir ~/.local/state/oshepherd/second-worker \
  --ollama-url http://127.0.0.1:11434 \
  --model llama3.2:3b
```

With a globally installed candidate, replace `uv run --locked oshepherd` with
`oshepherd`. Keep this process running. Wait for **Worker connected**; the
command saves its session privately in the selected state directory.
`--model` selects a model during discovery; the worker advertises all compatible
installed models, not just that one.

Do not copy another worker's saved state onto this machine. Each worker needs its
own invitation and session. After successful enrollment, delete the transferred
invitation copies; retain the worker state for restarts.

## 4. Verify registration and inference

In the Supabase dashboard's Table Editor, select the `oshepherd` schema. Check
`workers` for the additional worker and `worker_models` for its advertised models.
For local Docker, use Studio as described in the local walkthrough.

Run the Python request from [cloud walkthrough step 4](supabase-cloud-walkthrough.md#4-send-requests-through-the-hosted-gateway),
or use the [client walkthrough](supabase-client-walkthrough.md#4-send-a-request).
Registration alone does not prove inference works. If both workers advertise
the requested model, a successful response can come from either worker.

To test the second worker specifically, download a compatible model that no
other worker has, restart `join` so its inventory refreshes, and request that
exact model name in the Python example. Keep `share` running on the operator
machine for cleanup. A completed response for that unique model verifies the
new worker's inference path.

## Watch worker logs

Workers now log registration, job claims, execution starts and outcomes to the
terminal automatically. Each job event includes worker, job and attempt IDs, the
operation and model. Outcome events also include elapsed milliseconds and, when
applicable, a classified error code. Prompts, generated content, credentials and
raw exception messages are excluded; idle polling does not produce an event.

The same messages are saved beside the worker session file, replacing `.json`
with `.log` (or appending `.log` for other session filenames). Startup prints the
log path. For the examples in this guide:

| Worker | Log file |
| --- | --- |
| Cloud `share`, default worker name `local` | `.oshepherd/cloud/first-run/local.worker-session.log` |
| Remote `join` | `~/.local/state/oshepherd/second-worker/worker-session.log` |
| Local Docker `share` | `.oshepherd/local/end-user/operator/local.worker-session.log` |
| Second local `join` | `.oshepherd/local/second-local-worker/worker-session.log` |

On the second machine, follow the joined worker's file in another terminal:

```sh
tail -F ~/.local/state/oshepherd/second-worker/worker-session.log
```

Files have mode `0600` and rotate at 5 MiB, keeping three backups (`.1` through
`.3`). Restarts append to the current file. The file is also written by installed
background workers even though their service manager discards console output.
For `start-worker --backend supabase`, its location follows `WORKER_SESSION_FILE`.
This is a bounded local history, not a permanent audit log.

`job_succeeded` means Supabase acknowledged the result. `attempt_failed` means a
failure was acknowledged; a retry may follow. `attempt_cancelled` describes local
cancellation, and `attempt_abandoned` means the lease/session is stale or revoked.
`attempt_unconfirmed` means execution ended without a confirmed outcome, such as
a lost RPC response: inspect `oshepherd.jobs` for the authoritative state. Metadata
requests served directly by the gateway do not create worker execution events.

Restart an existing foreground worker with the updated checkout to enable these
logs. For an installed service, update its candidate package following the
[installation instructions](install.md), then restart the service.

## 5. Stop and restart the worker

Press Ctrl-C in the second worker's terminal to stop serving. Leave Ollama running
and restart without the invitation:

```sh
uv run --locked oshepherd join \
  --state-dir ~/.local/state/oshepherd/second-worker
```

The saved configuration remembers the installation, Ollama URL and selected
model. Use the same state directory each time. For a persistent service after
foreground testing, see [run in the background](setup-supabase.md#run-in-the-background).

If the invitation expires before enrollment, or enrollment reports an uncertain
outcome with no saved session, renew it on the operator machine:

```sh
uv run --locked oshepherd invite \
  --state-dir .oshepherd/cloud/first-run \
  --name second-worker --renew \
  --output .oshepherd/cloud/first-run/second-worker-renewed.invitation.json
```

Transfer that new file and repeat `join` with the same worker state directory.
If a session was already saved successfully, restart without an invitation.

## Local Docker variation

The [local walkthrough](supabase-walkthrough.md) uses loopback Supabase URLs.
Its invitations work on that host; transferring one to a physical second machine
does not make those URLs reachable. Use the cloud setup for the remote-machine
example above; do not edit invitation URLs.

To test a second worker on the same host, keep the local Supabase stack, gateway
and original `share` running. From the repository root, create an invitation:

```sh
uv run --locked oshepherd invite \
  --state-dir .oshepherd/local/end-user/operator \
  --name second-local-worker \
  --output .oshepherd/local/second-local-worker.invitation.json
```

In a separate terminal, start another Ollama server:

```sh
OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

In another terminal, ensure that server has the model and join with separate state:

```sh
OLLAMA_HOST=127.0.0.1:11435 ollama pull llama3.2:3b
uv run --locked oshepherd join \
  --invitation-file .oshepherd/local/second-local-worker.invitation.json \
  --state-dir .oshepherd/local/second-local-worker \
  --ollama-url http://127.0.0.1:11435 \
  --model llama3.2:3b
```

Verify as above. Restart with only
`uv run --locked oshepherd join --state-dir .oshepherd/local/second-local-worker`.
