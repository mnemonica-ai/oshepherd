# Supabase primary broker

Updated November 22, 2026. This index groups the accepted design and acceptance
specifications. Supabase is implemented as the primary onboarding path; Redis
remains supported with its existing low-level CLI defaults. The candidate is
experimental and unpublished.

Start at the [documentation index](../../README.md) for user instructions. The
[progress ledger](progress.md) owns dated results, including successful manual
local/cloud use, remote-client onboarding and worker logging. The
[next-steps plan](next-steps.md) owns remaining acceptance work. Historical Edge
CPU-limit and disconnect findings remain open until their specific checks pass.

## Decisions

- Use Supabase Postgres for the broker, Auth for worker identities, Storage for
  larger results and Edge for the default gateway. Models run in external Ollama.
- Preserve FastAPI/Celery/Redis as a legacy option; do not migrate jobs or change
  existing low-level command defaults implicitly.
- Keep the optional persistent FastAPI gateway on the same Supabase protocol.
  The current candidate still has a 135-second SQL synchronous maximum.
- Support all nine existing routes within declared limits, including System One.
  Do not silently replay requests on another backend.
- Provide async jobs for work outside synchronous limits, using an explicit
  status/result/download flow.
- Prioritize local CLI usability and private worker invitations. Keep cloud
  readiness, connected-worker readiness and real inference separately verified.
- Defer public publication, API expansion and hosted browser onboarding.

## Reading order

| Document | Purpose |
| --- | --- |
| [Local walkthrough](../../supabase-walkthrough.md) | Fresh Docker setup with Studio, Python inference and another local Ollama server |
| [Cloud walkthrough](../../supabase-cloud-walkthrough.md) | Supabase account/project setup, hosted inference, database inspection and restart |
| [Worker onboarding](../../supabase-worker-walkthrough.md) | Invite/join, remote and local workers, restarts and logs |
| [Client onboarding](../../supabase-client-walkthrough.md) | Named credentials, Python requests, re-export and revocation |
| [Next steps](next-steps.md) | Remaining acceptance and qualification work |
| [Progress ledger](progress.md) | Implemented behavior and dated evidence |
| [CLI onboarding](cli-onboarding.md) | Share/join design and implementation slices |
| [Development handoff](development-handoff.md) | Setup and contracts to preserve |
| [Implementation](implementation.md) | Broker, streaming, failure, security and resource design |
| [Installation](installation.md) | Hosting, shared bootstrap, recovery and later browser setup |
| [Compatibility](compatibility.md) | Existing routes and verification gates |
| [Experiments](experiments/README.md) | Detailed runbooks and historical measurements |

## What still prevents qualification?

The known Edge disconnect failure, incomplete fresh hosted/onboarding matrix
and wider performance/resource/cost evidence remain blockers. See
[next steps](next-steps.md) for concrete acceptance criteria. Local success and
bounded historical hosted tests must not be presented as full qualification.

[All plans](../index.md)
