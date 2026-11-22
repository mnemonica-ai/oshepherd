# Client walkthrough: give a script its own inference credentials

Create a separate application key for a script or service that sends inference
requests to an existing oshepherd gateway. You can revoke that key independently
of other clients and the original installation's `client.json`.

A client consumes inference; it does not run a worker. To contribute an Ollama
server, follow the [worker walkthrough](supabase-worker-walkthrough.md).

## 1. Start with a working installation

Complete the [cloud walkthrough](supabase-cloud-walkthrough.md). Keep `share`
running on the operator machine for inference and stored-result cleanup. These
commands run from that checkout and use `.oshepherd/cloud/first-run`, the state
directory created by that guide.

Use the checkout or the [installed `0.1.0rc1` candidate](install.md#install-the-010rc1-candidate).
Confirm the commands are available:

```sh
uv run --locked oshepherd client --help
```

For a globally installed candidate, replace `uv run --locked oshepherd` with
`oshepherd`. If your installation uses another state directory, substitute it in
every operator command below. For the default `share` state, omit `--state-dir`.

The consuming machine needs Python, the Ollama Python client and network access
to the gateway. It does not need Ollama running, oshepherd installed, Docker,
a Supabase account or an administrator token.

## 2. Create credentials on the operator machine

Use one name per consuming application:

```sh
uv run --locked oshepherd client create \
  --state-dir .oshepherd/cloud/first-run \
  --name remote-script \
  --output .oshepherd/cloud/first-run/remote-client.json

uv run --locked oshepherd client list \
  --state-dir .oshepherd/cloud/first-run
```

Look for `remote-script` with status `active`. Names contain 1–64 lowercase
letters, digits, hyphens or underscores, starting with a letter or digit.

The command reads `connection.json` from the selected installation directory;
use `--connection-file` if your private operator connection is stored elsewhere.
The output parent directory must already exist, belong to you and be writable
only by you. The command writes the export with mode `0600` and does not overwrite
unrelated files.

The export contains `gateway_url` and `application_key` (plus a format version).
It contains no operator credentials. The key is a bearer credential: anyone with
it can access the installation's inference gateway. Separate client keys provide
independent revocation; they do not configure per-client model restrictions or
quotas.

## 3. Transfer only the client export

For example, replace `user@client-host` with the script machine's SSH destination:

```sh
scp .oshepherd/cloud/first-run/remote-client.json \
  user@client-host:~/remote-client.json
```

On the consuming machine:

```sh
chmod 600 ~/remote-client.json
```

Keep the export outside source control and logs. Keep `connection.json`,
`installation.json` and the operator's `clients/` registry on the operator
machine; do not transfer the entire state directory.

## 4. Send a request

On the consuming machine, create a Python environment for the script, or install
`ollama` in its existing environment:

```sh
python3 -m venv ~/.venvs/oshepherd-client
~/.venvs/oshepherd-client/bin/python -m pip install ollama
```

Run this example there. Change `llama3.2:3b` if your workers serve another model:

```sh
~/.venvs/oshepherd-client/bin/python - <<'PY'
import json
from pathlib import Path
from ollama import Client

settings = json.loads((Path.home() / "remote-client.json").read_text())
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=145,
)

print("Models:", [model.model for model in client.list().models])
response = client.generate(
    model="llama3.2:3b",
    prompt="Say hello in one sentence.",
    options={"num_predict": 64},
)
print(response.response)
PY
```

Success means the model is listed and a generated response completes through the
gateway. The script never connects directly to the worker's Ollama port. Use the
same URL and Authorization header with `AsyncClient` or in your existing script.

If the gateway is unreachable, check its URL and the consuming machine's network
access. If authentication is rejected, verify that you transferred the intended
export and its key has not been revoked. If no worker serves the requested model,
check the worker processes and their model inventories.

## 5. Re-export or recover credentials

If creation was interrupted, repeat the original command with the same name and
output. It resumes the recorded operation; an identical private export can be
reused. To copy an active client's existing key to a fresh file:

```sh
uv run --locked oshepherd client create \
  --state-dir .oshepherd/cloud/first-run \
  --name remote-script \
  --output .oshepherd/cloud/first-run/remote-client-copy.json
```

The same name re-exports the same key; it does not create a second identity or
rotate the credential. Use a new name for another application.

`client list` shows names, key IDs and recorded statuses from this operator's
local registry. It is not a live inventory of all keys in Supabase, and it does
not list the original installation's `client.json`. Keep the operator state
backed up privately so you retain the mapping needed to manage named clients.

## 6. Revoke this client's access

On the operator machine:

```sh
uv run --locked oshepherd client revoke \
  --state-dir .oshepherd/cloud/first-run \
  --name remote-script

uv run --locked oshepherd client list \
  --state-dir .oshepherd/cloud/first-run
```

Expect status `revoked`. Repeat the Python example on the consuming machine: its
next authenticated request should be rejected. Other client keys and the
installation's original `client.json` remain valid. Revocation blocks subsequent
authentication; do not use it as a guarantee that an already accepted request
has been cancelled.

If revocation is interrupted, retry it with the same name. Revoked names stay
reserved. For a replacement, create a new name such as `remote-script-v2` and
transfer its new export. Deleting an exported JSON file alone does not revoke
its key.

## Local Docker variation

After completing the [local walkthrough](supabase-walkthrough.md), use its state
directory for all client commands:

```sh
uv run --locked oshepherd client create \
  --state-dir .oshepherd/local/end-user/operator \
  --name local-script \
  --output .oshepherd/local/end-user/local-client.json
```

Run the Python example on the same host, changing its settings path to
`Path(".oshepherd/local/end-user/local-client.json")` from the repository root.
Keep the local Supabase containers, Edge gateway and `share` running.
Use `--state-dir .oshepherd/local/end-user/operator --name local-script` for
revocation, and the same state directory for listing.

The default export points to a loopback gateway. It cannot be used from a
physical remote machine as-is; use the hosted HTTPS gateway in the cloud example
for that scenario.
