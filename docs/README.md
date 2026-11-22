# Documentation

Supabase is oshepherd's primary setup path. Redis/Celery/FastAPI remains supported
with its existing CLI defaults. The Supabase candidate is currently unpublished;
use a checkout or a locally built wheel as described in [installation](install.md).

## Start and use an installation

| Task | Guide |
| --- | --- |
| Install from a checkout or candidate wheel | [Installation](install.md) |
| Run everything locally with Docker and inspect the database in Studio | [Local Supabase walkthrough](supabase-walkthrough.md) |
| Use Supabase Cloud with Ollama on your machine | [Cloud walkthrough](supabase-cloud-walkthrough.md) |
| Add another machine that serves inference | [Worker onboarding: invite/join](supabase-worker-walkthrough.md) |
| Give a script its own inference key | [Client onboarding: create/list/revoke](supabase-client-walkthrough.md) |
| View and follow worker logs | [Worker logs](supabase-worker-walkthrough.md#watch-worker-logs) |
| Restart, install a background service, or disable an installation | [Operations reference](setup-supabase.md) |
| Use the Redis backend | [Redis setup](setup-redis.md) |

An operator manages an installation. A worker executes jobs through its local
Ollama server. A client sends inference requests. Client keys and worker
invitations are separate credentials; choose the onboarding guide for that role.

The walkthroughs use separate private state directories:

| Example | State directory |
| --- | --- |
| Manual local operator | `.oshepherd/local/end-user/operator` |
| Cloud operator | `.oshepherd/cloud/first-run` |
| Remote joined worker | `~/.local/state/oshepherd/second-worker` |
| Commands using the default operator directory | `~/.local/state/oshepherd/share` (or under `XDG_STATE_HOME`) |

Pass the same `--state-dir` when managing or resuming an installation. Transfer
only an exported client file or worker invitation, not the operator directory.

## Understand the database and API

- [Architecture and data model](plans/supabase/implementation.md#5-data-model-and-atomic-operations)
  explains the broker's records and transitions.
- [Versioned SQL migrations](../oshepherd/backends/supabase/release/migrations/)
  define the database schema. The [release manifest](../oshepherd/backends/supabase/release/manifest.json)
  selects migration histories; existing migrations are immutable.
- [Supabase protocol](../protocol/supabase/v1/README.md) defines RPCs, roles,
  limits, leases, output, asynchronous jobs and cleanup.
- [Supported routes](../README.md#use-the-ollama-python-package) summarizes the
  client surface. [Protocol fixtures](../protocol/README.md) explain compatibility
  with the original Redis API.

For inspection, select the `oshepherd` schema in Studio or the cloud Table Editor.
Start with `workers`, `worker_models`, `jobs` and `chunks`. The `oshepherd_api`
schema contains the RPC functions used by gateways and workers.

## Develop and validate

| Document | Purpose |
| --- | --- |
| [Development handoff](plans/supabase/development-handoff.md) | Repository map, checks and contracts to preserve |
| [Disposable local acceptance](setup-supabase-local.md) | Automated fresh-stack test; removes its own database after testing |
| [Progress ledger](plans/supabase/progress.md) | Dated validation evidence and current qualification status |
| [Next steps](plans/supabase/next-steps.md) | Remaining work and acceptance criteria |
| [Design and acceptance plans](plans/supabase/index.md) | Architecture, installation and compatibility specifications |
| [Experiments and runbooks](plans/supabase/experiments/README.md) | Detailed procedures and historical measurements |

Use the manual local walkthrough for a database you want to keep. The disposable
acceptance runner is a development tool with a different lifecycle.

The progress ledger owns validation claims; the next-steps document owns pending
work. Dated experiment reports retain the results of their original revisions.
Successful manual use does not close the remaining Edge cancellation and resource
qualification gates.
