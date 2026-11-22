# Cloud walkthrough: Ollama through Supabase Cloud

Start with a Supabase account and end with Python requests through a hosted
gateway to Ollama on your machine. Supabase hosts Postgres, Auth, Storage and the
Edge gateway; the oshepherd worker and Ollama stay on your machine and connect
outbound. No inbound Ollama port or tunnel is needed.

For a fully local installation with Docker and Studio, use the separate
[local walkthrough](supabase-walkthrough.md).

This guide runs the `0.1.0rc1` candidate from this checkout. For an
installed package, follow [candidate installation](install.md#install-the-010rc1-candidate).
Docker, the Supabase CLI and `psql` are not required for this cloud example.

## 1. Create an organization and access token

1. Sign in to the [Supabase dashboard](https://supabase.com/dashboard).
2. Create an organization such as `oshepherd-test`, choosing the **Free** plan.
   You can also use an existing Free organization.
3. Leave project creation to oshepherd in step 3. If you already created a
   dedicated test project, select it in the wizard instead of creating another.
4. Open [Account → Access Tokens](https://supabase.com/dashboard/account/tokens)
   and create a personal access token named `oshepherd-cloud-test`.

For the initial candidate test, the documented path uses a full-access token
(shown as Legacy in Supabase) with a short expiration covering the test. It can
access every organization/project your account can access; the minimum
fine-grained permission set for this wizard has not been qualified. Keep the
token private and enter it only at the CLI's hidden prompt. It is an account
personal access token, not a project's publishable or secret API key. See
[Supabase's token documentation](https://supabase.com/docs/guides/platform/personal-access-tokens).

`oshepherd share` makes direct HTTPS requests to Supabase's Management API for
setup. It does not run Supabase CLI commands or reuse `supabase login`.
Manual token entry is the current supported flow; browser authentication is
[planned follow-up work](plans/supabase/next-steps.md#later-optional-browser-onboarding-and-distribution).

## 2. Prepare oshepherd and Ollama

You need Python 3.12, uv and Ollama. Run all commands below from the repository
root unless a step says otherwise:

```sh
uv sync --locked --extra supabase
ollama list
```

Keep the Ollama app running, or run `ollama serve` in a separate terminal.
The example uses `llama3.2:3b`. Download it only if it is missing:

```sh
ollama pull llama3.2:3b
```

Verify direct inference:

```sh
ollama run llama3.2:3b "Say hello in one sentence."
```

If you just completed the local walkthrough, stop its `share` and
`supabase functions serve` processes with Ctrl-C and run `supabase stop` from
its local project directory. Then return to the repository root. Keep Ollama
running. The cloud example uses separate state and does not need local containers.

## 3. Create the hosted installation

In Terminal 1, from the repository root:

```sh
uv run --locked oshepherd share \
  --state-dir "$PWD/.oshepherd/cloud/first-run" \
  --ollama-url http://127.0.0.1:11434 \
  --model llama3.2:3b
```

Use this same state directory on retries and later restarts. Do not pass a local
Docker `--connection-file`.

Follow the prompts:

1. Paste the personal access token into the hidden prompt.
2. At **Project number**, choose **0 — Create a new Supabase project**.
3. Select the existing Free organization you created in step 1.
4. Confirm Free-plan use. If the provider does not report its plan, check the
   dashboard before confirming the fallback prompt.
5. Choose a nearby region from the offered list and name the project
   `oshepherd-cloud-test`.
6. Review and authorize the exact project name, including its recovery suffix.
   The wizard generates and saves the database password privately.
7. Review the selected project and authorize installation.

Oshepherd installs its schema, private Storage and cleanup schedules, deploys
the Edge gateway, and enrolls this worker. Wait for **cloud ready**, **worker
connected**, **storage cleanup active**, and finally **inference verified**.

The final message includes an endpoint like
`https://<project-ref>.supabase.co/functions/v1/ollama` and the path to
`.oshepherd/cloud/first-run/client.json`. Keep Terminal 1 running: it supervises
the worker and result cleanup. The selected model is used for verification;
the worker advertises all compatible installed models.

Worker job activity appears in this terminal and is saved in
`.oshepherd/cloud/first-run/local.worker-session.log`. Follow it from another
terminal with `tail -F .oshepherd/cloud/first-run/local.worker-session.log`.
See [worker logs](supabase-worker-walkthrough.md#watch-worker-logs) for events,
rotation and joined-worker log locations.

For an existing project, choose its entry instead of option 0. The wizard still
requires target confirmation and installs oshepherd's resources. This tests
existing-project setup rather than automatic project creation.

## 4. Send requests through the hosted gateway

In Terminal 2, from the repository root:

```sh
uv run --locked python - <<'PY'
import json
from pathlib import Path
from ollama import Client

settings = json.loads(
    Path(".oshepherd/cloud/first-run/client.json").read_text()
)
client = Client(
    host=settings["gateway_url"],
    headers={"Authorization": "Bearer " + settings["application_key"]},
    timeout=145,
)

print("Gateway:", settings["gateway_url"])
print("Models:", [model.model for model in client.list().models])

print("\nNon-streaming response:")
print(client.generate(
    model="llama3.2:3b",
    prompt="Explain distributed inference in one sentence.",
    options={"num_predict": 64},
).response)

print("\nStreaming response:")
for chunk in client.chat(
    model="llama3.2:3b",
    messages=[{"role": "user", "content": "Explain a queue in one sentence."}],
    stream=True,
    options={"num_predict": 64},
):
    print(chunk.message.content or "", end="", flush=True)
print()
PY
```

Success means the printed URL is hosted HTTPS, your model is listed, and both
responses complete while local Supabase containers remain stopped. The
application key comes from `client.json`; keep that file private. Use the same
URL and headers with Ollama's `AsyncClient` for asynchronous Python calls.

### Send requests from a remote script

Follow the [client onboarding walkthrough](supabase-client-walkthrough.md) to
create a named key, transfer it, send requests, and revoke it independently.
Use this installation's `.oshepherd/cloud/first-run` state directory. Transfer
only the exported client file; the operator state stays on this machine.

To add a machine that serves inference, follow the separate
[worker onboarding walkthrough](supabase-worker-walkthrough.md).

## 5. Inspect the cloud database

Open the selected project in the Supabase dashboard. In **Table Editor**, select
the `oshepherd` schema and inspect `workers`, `worker_models`, `jobs` and `chunks`.
The `oshepherd_api` schema contains RPC functions. Under **Edge Functions**, look
for `ollama`. Send another request and refresh the job list. Stored output is
subject to cleanup, so old chunks may no longer be present.

## 6. Verify restart and resume

Press Ctrl-C in Terminal 1, then repeat the exact `share` command from step 3.
Wait for inference verification and repeat step 4. The existing installation and
saved session should be reused; no new project is needed.

Keep `.oshepherd/cloud/first-run` when recovering interrupted setup. The wizard
reconciles uncertain creation outcomes using saved state; deleting it can lose
that recovery information. A separate installation needs a separate directory.

## 7. Optional broader endpoint checks

Install `embeddinggemma:latest` if missing, then restart `share` so it discovers
the model:

```sh
ollama pull embeddinggemma:latest
```

From the repository root, run:

```sh
uv run --locked python examples/supabase_client.py \
  --client-file .oshepherd/cloud/first-run/client.json \
  --model llama3.2:3b --embedding-model embeddinggemma:latest
```

This checks generate/chat JSON and streaming, legacy embeddings and metadata.
Add `--decision-model tev1:4b` only if a compatible decision model is installed.
Use `embeddings()` for the supported legacy endpoint; `/api/embed` and model
management are outside the current endpoint set.

## 8. Stop and continue later

Ctrl-C stops this worker and cleanup, leaving the cloud project in place. Resume
with the same command and state directory. Setup saves the personal access token
privately for recovery; a completed installation can resume without that token,
so it can be revoked after setup. Later administrative actions may need fresh
authorization.

For another physical machine, background services, or installation disablement,
see the [hosted operations guide](setup-supabase.md). That guide's examples use
the default state directory: substitute `.oshepherd/cloud/first-run` for this
walkthrough, and pass its `connection.json` when an operator command requires it.
Background services require an installed candidate wheel, not an editable checkout.

## Limits and troubleshooting

- **Setup error:** retain the state directory and retry the same command. Report
  the failing stage and redacted error; do not share tokens or private JSON files.
- **Unauthorized request:** use the application key from this installation's
  `client.json`, not the personal access token or a Supabase project key.
- **No eligible worker/model:** keep Ollama and `share` running and verify the
  model directly before retrying through the gateway.
- **Slow requests:** the synchronous budget is 135 seconds, including queueing
  and model loading. Increasing the client timeout does not extend it.
- **Large results:** Edge request/response bodies are capped at 1 MiB. The
  separate asynchronous `/jobs` flow supports retained results up to 32 MiB.
- **Stream disconnects and resource limits:** reliable Edge disconnect
  cancellation and wider resource qualification remain open. A successful manual
  walkthrough is functional evidence, not full release qualification. See the
  [remaining plan](plans/supabase/next-steps.md).
