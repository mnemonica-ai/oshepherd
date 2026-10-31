# Share Ollama through Supabase and optional Vercel

This guide describes the **unpublished experimental `0.0.25a1` candidate**. The
published `0.0.24` package does not contain this onboarding workflow. The candidate
provides `share`, `invite`, `join` and optional user services. Both hosted gateways
remain experimental: closing a client stream does not yet reliably cancel the
underlying inference.

Supabase is required. Its Edge gateway is the default; the optional Vercel gateway
uses the same Supabase backend. Ollama stays on your machine, with outbound worker
connections to Supabase. No public Ollama port or inbound tunnel is required.

## Start with the candidate

Have uv, Ollama and at least one installed model available on macOS or Linux. With
a verified candidate wheel supplied or built using the
[distribution instructions](plans/supabase/experiments/distribution.md), start from
any directory:

```sh
uvx --from '/absolute/path/oshepherd-0.0.25a1-py3-none-any.whl[supabase]' oshepherd share
```

Use that same `uvx --from ... oshepherd` prefix for the other commands below. To
keep the candidate installed instead:

```sh
uv tool install --python 3.12 '/absolute/path/oshepherd-0.0.25a1-py3-none-any.whl[supabase]'
oshepherd share
```

After an explicitly approved publication, a pinned PyPI `uvx --from` command can
replace the wheel path. Do not assume that publication has happened. A source
checkout can run the Edge journey with `uv sync --locked --extra supabase` and
`uv run --locked oshepherd share`; guided Vercel deployment and durable service
runtime creation require an installed candidate wheel rather than an editable
checkout.

## Share this machine

1. Start `oshepherd share`. It discovers local Ollama models and asks which to use
   for the first inference check. It never downloads a model. Selection of a test
   model does not restrict the worker's advertised compatible model inventory.
2. Enter your Supabase personal access token at the hidden prompt. Choose an
   existing project or create one in an existing/new organization. No provider
   CLI, handwritten JSON, database URL or `psql` installation is needed.
3. For creation, choose a name and a provider-listed region. Review the exact
   resource name, including its unique recovery suffix, before authorizing it.
   The database password is generated and stored privately. Account limits can
   still prevent project creation; no paid upgrade is requested.
4. Supabase's organization API verifies Free when it returns a plan. An explicit
   non-Free plan is rejected. If the optional plan field is absent, confirm Free
   in the dashboard and attest at the prompt. Review and authorize setup on the
   selected project. The wizard discovers API keys/schemas and creates the managed
   owner identity without asking you to copy UUIDs.
5. Wait for separate cloud, worker, cleanup and real-inference readiness messages.
   The authenticated endpoint and private client configuration path appear only
   after inference succeeds. Keep that client file private; it contains access
   credentials.
6. Leave the terminal running to share. Ctrl-C stops this worker and cleanup; it
   does not remove cloud resources or revoke other workers.

Use `oshepherd share --gateway vercel` for the optional Vercel path. After Supabase
selection, enter a Vercel token privately, select a verified Hobby account/team,
and create or select the gateway project. Review the destination and Production
secret configuration before deployment. The adapter uses provider APIs and the
verified installed package; no Vercel CLI or manually prepared deployment wheel is
needed. Existing unrelated deployments/configuration are preserved and ownership
collisions are rejected. Neither flow automatically adopts a foreign installation.

State defaults to `$XDG_STATE_HOME/oshepherd/share`, or
`~/.local/state/oshepherd/share` when unset. Files are private to your user. Another
installation needs its own `--state-dir`. Rerun the same command to resume.
Creation intent is saved before dispatch; an uncertain response is reconciled
against the exact target instead of issuing another creation request. If the
outcome remains unresolved, retain that state and retry later; do not delete the
journal to force duplicate creation.

Completed guided installations restart using their existing sessions without
provider Management credentials. Initial or interrupted setup revalidates the
selected target and obtains confirmation before continuing. Revoked provider
credentials can be replaced through a fresh hidden prompt.

Storage cleanup runs on the operator host while `share` runs. Joined workers have
no cleanup administrator key. Keep the operator running even when inference is
served by another machine, or use the background share service below. `--check`
verifies first inference and stops both processes; it is a smoke check.

## Connect another machine

On the operator machine, issue an invitation with a unique worker name:

```sh
oshepherd invite --name second-machine --output /private/path/second-machine.invitation.json
```

Transfer this file privately to the second machine, preserving mode `0600`. It is
a one-use bearer credential, not a public link. It contains only project/public
configuration and the enrollment challenge; no provider, database or application
administrator credentials. Supabase Auth enforces its expiry.

With the same candidate installed and Ollama running on that machine:

```sh
oshepherd join --invitation-file /private/path/second-machine.invitation.json
```

A subsequent `oshepherd join` restarts its saved worker without a new invitation.
It reports actual worker registration, not a gateway inference test. Keep it in
the foreground or install the optional joined-worker service. Use `--state-dir`
consistently for multiple workers. `--ollama-url` on first setup selects a different
reachable Ollama server; `--model` selects the discovery/test model.

Expired or ambiguously consumed enrollment requires an explicitly renewed
invitation (`invite --renew` with a new output path). Existing sessions retain
their worker identity; a different worker needs a separate state directory. Never
put invitation contents in shell arguments or public URLs.

## Optional background operation

Finish foreground onboarding and stop it before installing a service. Services
use macOS launchd or Linux user systemd, start at user login, and need no root
privileges. User login/session-manager availability remains an operating-system
requirement; this does not configure system-wide boot startup or Linux lingering.

For the default operator state, with `XDG_STATE_HOME` unset:

```sh
oshepherd service install --kind share --state-dir "$HOME/.local/state/oshepherd/share"
oshepherd service status
oshepherd service stop
oshepherd service start
```

For a joined worker, use `--kind join` and its worker state directory. Specify a
unique `--name` when managing multiple services. The command prepares an owned,
durable uv runtime when the current interpreter is temporary, using the exact
installed candidate. Unit definitions contain no credentials; runtime state and
logs remain private.

`oshepherd service uninstall` removes the owned service definition. It preserves
cloud resources, credentials, worker state and the owned Python runtime. Service
manager status is operational evidence; it does not qualify hosted cancellation.

## Existing operator configurations

Prepared installations retain the advanced flow:

```sh
oshepherd share --connection-file /private/path/connection.json \
  --state-dir /private/path/operator --model llama3.2:3b
```

Use an already installed model. Initial prepared connections require an explicit
`--owner-subject`; guided setup creates its owner automatically. `--provider-file`
selects an ownership-bound gateway adapter. A prepared connection without a
private database URL can use `--database-provider-file` for Management SQL.
Existing private database/psql setups remain supported. `--wheel` is an optional
advanced Vercel artifact override, not a normal onboarding requirement.

Use the same connection/state options when issuing invitations. Disabled
installations and revoked workers are rejected. Retired qualification credentials
and removed gateway receipts must not be reused for sharing.

## Readiness

The source implementation covers provider creation/selection, worker enrollment,
foreground cleanup, optional services and candidate publication tooling. Fresh
hosted acceptance, installed service checks and publication evidence must be
recorded separately in the [progress ledger](plans/supabase/progress.md). The
[adoption plan](plans/supabase/cli-onboarding.md) remains open until its user-facing
acceptance and distribution requirements pass. Hosted OAuth/buttons are a later
optional onboarding path.
