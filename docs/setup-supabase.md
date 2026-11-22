# Hosted Supabase setup and operations

For a complete first-run example, follow the
[Supabase Cloud walkthrough](supabase-cloud-walkthrough.md). This reference
covers hosted setup, additional workers, services and disablement. For Docker
containers on your machine, use the [local walkthrough](supabase-walkthrough.md).

> **Experimental.** This needs the `0.0.25a1` candidate, which is not on PyPI yet.
> Closing a streaming client does not yet reliably cancel the inference behind it,
> so a cancelled request can keep a worker busy until it finishes.

Supabase hosts everything except the models: the API endpoint (a Supabase Edge
Function), the request queue, worker logins and stored results. Ollama stays on
your machines. Each worker connects out to Supabase, so you open no ports and
need no tunnel or server of your own.

```text
Ollama client ──HTTPS──> Supabase Edge Function ──> Supabase database
                                                          ↑ outbound
                                  oshepherd worker ───────┘ ──> local Ollama
```

## What you need

- macOS or Linux.
- oshepherd `0.0.25a1` installed with `[supabase]`. See
  [Install the 0.0.25a1 candidate](install.md#install-the-0025a1-candidate).
- [Ollama](https://ollama.com/download) running, with at least one model pulled.
  oshepherd never downloads models.
- A [Supabase](https://supabase.com) account on the Free plan, and a personal access
  token from **Account → Access Tokens**
  ([supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)).
  oshepherd keeps a private copy in its state directory for setup and recovery. A
  finished installation restarts without it, so you can revoke the token afterwards.
  If a later step needs one, you are asked for a new token.

No Supabase CLI, database password, `psql` or hand-written config file is needed.

### Moving from a local Docker test

Stop the local gateway and worker with Ctrl-C, then run `supabase stop` from
that local project's directory. Keep Ollama running. For a fresh hosted test
from this checkout, use a separate state directory:

```sh
uv run --locked oshepherd share \
  --state-dir "$PWD/.oshepherd/cloud/first-run" --model llama3.2:3b
```

Run this from the repository root. Choose a new dedicated test project in the
wizard below. Do not pass the local `--connection-file`. Your hosted client
configuration will be `.oshepherd/cloud/first-run/client.json`; use that path
in the Python example instead of the default path shown below. Keep this state
directory and repeat the same command to resume or recover an interrupted setup.
Pass the same state directory to later operator commands such as `invite`.

## 1. Share this machine

```sh
oshepherd share
```

The command walks you through setup:

1. **Pick a model** from the ones Ollama already has. It is used for the first
   test request only; the worker serves all of your installed models.
2. **Paste your Supabase access token** at the hidden prompt.
3. **Choose a project.** Select an existing one, or create one in an existing or
   new organization. To create one, give it a name and pick a region. Review the
   exact resource name before you confirm; it ends in a unique suffix used for
   recovery. The database password is generated and stored for you.
4. **Confirm the Free plan.** oshepherd rejects a paid plan. If Supabase doesn't
   report the plan, check it in the dashboard and confirm at the prompt.
5. **Authorize setup** on the selected project. oshepherd installs its database
   schema and Edge Function and creates an owner identity for you.
6. **Wait for the readiness messages.** They are, in order: cloud ready, worker
   connected, storage cleanup active, and inference verified. That last message
   shows your endpoint and the path of a private client file:

   ```text
   Inference verified. Endpoint: https://<project-ref>.supabase.co/functions/v1/ollama
   Private client configuration: ~/.local/state/oshepherd/share/client.json
   ```

7. **Leave the terminal open.** This process is the worker, and it also cleans up
   stored results. Ctrl-C stops both, but leaves your cloud resources and other
   workers in place.

To check setup without staying online, use `oshepherd share --check`. It stops
after the first successful request.

To resume after a stop or an interrupted setup, run `oshepherd share` again. It
picks up the saved state in `~/.local/state/oshepherd/share`, or under
`$XDG_STATE_HOME` if set. If a creation request had an uncertain outcome,
oshepherd checks for the existing project instead of creating a second one. Keep
the state directory; deleting it to force a fresh start can leave duplicates. For
a second, separate installation, pass a different `--state-dir`.

## 2. Send a request

`client.json` holds the gateway URL and an application key. Keep it private;
anyone with the key can use your workers. Every request needs the key as a bearer
token.

```python
import json
from pathlib import Path

import ollama

config = json.loads(
    (Path.home() / ".local/state/oshepherd/share/client.json").read_text()
)
client = ollama.Client(
    host=config["gateway_url"],
    headers={"Authorization": "Bearer " + config["application_key"]},
)
print(client.generate(model="llama3.2:3b", prompt="Why is the sky blue?")["response"])
```

Or with curl:

```sh
CLIENT=~/.local/state/oshepherd/share/client.json
curl "$(jq -r .gateway_url "$CLIENT")/api/generate" \
  -H "Authorization: Bearer $(jq -r .application_key "$CLIENT")" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2:3b","prompt":"Why is the sky blue?","stream":false}'
```

The gateway serves generate, chat, embeddings, System One, tags, ps, show and
version. Use a model your workers actually have.

**Limits:**

- Each request has a 135-second budget.
- Request bodies and Edge response bodies are each capped at 1 MiB. For larger or
  longer jobs, use the asynchronous `/jobs` endpoint, which stores results of up
  to 32 MiB for you to download.
- Your Supabase Free plan limits also apply.

## 3. Give another application access

For a complete example, follow the [client onboarding walkthrough](supabase-client-walkthrough.md).

On the installation host, create a separate application key for each script or
service that sends requests to the gateway.

```sh
oshepherd client create --name remote-script --output remote-client.json
oshepherd client list
```

These commands use the default `share` state directory. If you started `share`
with `--state-dir`, pass the same option to each client command. The commands
read `connection.json` in that directory unless you supply
`--connection-file` explicitly.

Transfer `remote-client.json` privately to the consuming machine and keep it
at mode `0600`. Point the Python example above at that file. It carries the
HTTPS gateway URL and a bearer key. Keep the installation state and its
operator credentials on the installation host, and keep the export out of
version control.

```sh
oshepherd client revoke --name remote-script
```

Revocation blocks subsequent authenticated requests made with that client's
key. Other clients and the installation's `client.json` keep their keys.
`client list` shows the names, key IDs and recorded status in the selected
local state. If creation is interrupted, repeat the command with the same
name and output. You can export an active client's existing key to a fresh
file by using the same name. Choose a new name after revocation.

## 4. Add another inference worker

For a complete example, follow the [worker onboarding walkthrough](supabase-worker-walkthrough.md).

Use `invite` and `join` to add another machine that runs Ollama and serves
inference. On the machine running `share`, create a unique worker invitation.

```sh
oshepherd invite --name second-machine --output ~/second-machine.invitation.json
```

Copy the file to the other machine privately, for example with `scp`, and keep it
at mode `0600`. It works once, it expires, and anyone holding it can join a worker.
It contains no Supabase token or administrator credentials. Don't paste its
contents into chat, shell arguments or URLs.

On the other machine, with oshepherd installed and Ollama running:

```sh
oshepherd join --invitation-file ~/second-machine.invitation.json
```

Once it reports the worker registered, it serves requests while it runs. To
restart it later, run `oshepherd join` without the file. Its state lives in
`~/.local/state/oshepherd/worker`. If an invitation expired, or you are unsure
whether it was used, create a new one with `invite --renew` and a new `--output`
path.

Keep `share` running on the first machine even if other machines do all the
inference: it runs the stored-result cleanup. The background service below can
take that over.

## Run in the background

After foreground setup works, stop it with Ctrl-C and install a per-user service.
It starts at login and needs no root access. It uses launchd on macOS and systemd
user units on Linux.

```sh
oshepherd service install --kind share --state-dir ~/.local/state/oshepherd/share
oshepherd service status
oshepherd service stop
oshepherd service start
oshepherd service uninstall
```

On a joined machine, use `--kind join --state-dir ~/.local/state/oshepherd/worker`.
For more than one service on a machine, give each a `--name`.

Uninstalling a service removes only its service definition. Your cloud resources,
credentials and worker state stay in place, so `oshepherd share` or `join` still
works in the foreground. On Linux, the service runs only while you are logged in,
unless you enable lingering yourself (`loginctl enable-linger`).

## Stop, disable and remove

| Goal | Command |
| --- | --- |
| Pause this worker | Ctrl-C, or `oshepherd service stop` |
| Disable the installation: refuse new requests, cancel running work, revoke its keys | `oshepherd supabase uninstall --connection-file ~/.local/state/oshepherd/share/connection.json --state-dir ~/.local/state/oshepherd/share` |
| Remove everything | Disable it as above, then delete the project in the Supabase dashboard |

Disabling keeps the data, schema and Edge Function in the project. A disabled
installation cannot be re-enabled; use a new `--state-dir` to set up again.

## Advanced: an existing operator configuration

If you already have a prepared operator connection file, skip the wizard:

```sh
oshepherd share --connection-file /private/path/connection.json \
  --state-dir /private/path/operator --model llama3.2:3b
```

A first run with a prepared connection also needs `--owner-subject`, the Supabase
Auth user ID to own the installation. `--provider-file` selects a gateway deployment
adapter. A connection without a database URL can apply its schema through the
Management API with `--database-provider-file`. Pass the same connection and
state options to `invite`. The [operator runbook](plans/supabase/experiments/operator-lifecycle.md)
covers the separate `oshepherd supabase` commands: upgrade, key rotation and
worker revocation.

## Status

Local/containerized service tests and earlier inference checks have passed;
later full local reruns hit the Edge CPU limit. On November 22, the user reported
the manual local and cloud walkthroughs working. This is manual functional
feedback; the broader hosted acceptance matrix and stream-cancellation fix
remain open. The [progress ledger](plans/supabase/progress.md) has the evidence.
