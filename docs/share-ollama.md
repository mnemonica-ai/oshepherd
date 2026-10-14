# Share Ollama with the experimental Supabase backend

This guide describes the source candidate, not the currently published PyPI
release. The CLI now provides `share`, `invite` and `join`. Automatic provider
project creation, a guided Vercel setup and background service installation remain
unfinished. Both hosted gateways remain experimental: closing a client stream
does not yet reliably cancel the underlying inference.

Supabase is required. Its Edge gateway is the default; an optional Vercel gateway
still uses Supabase. Ollama stays on your machine, with outbound worker connections
to Supabase. No public Ollama port or inbound tunnel is required.

## Share this machine

For now, use a checkout of this candidate:

```sh
uv sync --locked --extra supabase
uv run --locked oshepherd share
```

Have Ollama running with at least one model already installed. The wizard discovers
models and asks which to use for its first inference check; it never downloads a
model. The worker advertises its compatible installed models, so selecting a test
model does not limit which models clients can request.

The guided path currently supports an **existing Supabase project on a Free
organization**, using Edge, on a POSIX operator host (macOS/Linux). It needs `psql` on your PATH, a Supabase personal access
token and the project's Postgres connection URL. Enter credentials at hidden
prompts. It does not read your provider CLI's credential store automatically.

1. Select your model and connect Supabase with the hidden token prompt.
2. Choose the project from your accessible organizations. Prefer a dedicated
   project; existing Oshepherd gateway/secret collisions are refused.
3. Enter its Postgres URL privately. The connection must identify that project and
   use TLS. The wizard discovers API keys and exposed schemas for you.
4. Review the target and proposed changes. Verify the organization is on Free in
   your dashboard and confirm. The wizard cannot independently verify Supabase
   billing and does not change your plan.
5. Wait for separate cloud, worker, cleanup and real-inference readiness messages.
   The endpoint and private client configuration path appear after inference succeeds.
6. Leave the terminal running while sharing. Ctrl-C stops this worker and its
   cleanup process; it does not remove the installation or revoke other workers.

State defaults to `$XDG_STATE_HOME/oshepherd/share`, or
`~/.local/state/oshepherd/share` when that variable is unset. Files are private to
your user. A different installation needs a separate `--state-dir`. Rerun the same
command to resume; existing identity and sessions are retained. A completed guided
installation restarts without its Management token or `psql`. Initial or interrupted
setup revalidates its target and asks for confirmation before continuing setup.

Storage cleanup runs on the operator host while `share` runs. If only other joined
machines remain online, keep the operator's maintenance process running using the
[operator runbook](plans/supabase/experiments/operator-lifecycle.md). Joined workers
cannot run administrative cleanup. `--check` performs the first inference and
stops both local processes; it is a smoke check, not background sharing.

## Connect another machine

On the operator machine, create an invitation with a unique worker name:

```sh
uv run --locked oshepherd invite --name second-machine \
  --output .oshepherd/second-machine.invitation.json
```

Transfer that file privately to the second machine, preserving mode `0600`. It is
a one-time bearer credential, not a public link. It contains the project's
publishable configuration and enrollment challenge, not provider, database or
application administrator credentials. Its expiry is enforced by Supabase Auth.

With this candidate installed and Ollama running on the second machine:

```sh
oshepherd join --invitation-file /private/path/second-machine.invitation.json
```

For a second source checkout use `uv run --locked oshepherd join` with the same
arguments. A subsequent `oshepherd join` restarts the saved worker without another
invitation. `join` reports actual worker registration but does not claim a
gateway inference test. Keep it in the foreground; Ctrl-C stops that worker.

Use `--state-dir` consistently when maintaining more than one worker configuration.
Use `--ollama-url` on first setup for a different reachable Ollama server, and
`--model` to avoid interactive model selection. Restart with the saved configuration.
Expired or ambiguously consumed enrollment requires an explicitly renewed
invitation (`invite --renew` with a new output path); an existing valid session is
reused. Never put invitation contents in command arguments or a public URL.

## Existing operator installations and optional Vercel

Prepared installations can use the same sharing workflow:

```sh
uv run --locked oshepherd share \
  --connection-file /private/path/connection.json \
  --state-dir /private/path/operator --model llama3.2:3b
```

The model must already exist on that Ollama server. On initial setup with a
prepared connection, pass the explicit `--owner-subject` or use the guided flow
to create its managed owner. An existing configured gateway is verified. To
deploy through an existing ownership adapter, also supply `--provider-file`; the
Vercel adapter additionally requires `--wheel` with a verified candidate wheel and
an explicitly configured gateway URL. See the [adapter runbook](plans/supabase/experiments/local-installer.md).
These advanced options do not constitute guided Vercel provisioning.

When creating invitations for a prepared installation, pass the same
`--connection-file` and `--state-dir` to `invite`. Disabled installations and
revoked workers are rejected. The previously removed qualification gateways are
not available for sharing and their retired credentials must not be reused.

## What comes next

The target published experience is one uv entry with no checkout. New project
creation, guided Vercel selection, eliminating the database URL/psql prerequisite,
optional background services and fresh hosted user acceptance are still required.
The [adoption plan](plans/supabase/cli-onboarding.md) tracks those gaps separately
from hosted qualification and later browser-based installation.
