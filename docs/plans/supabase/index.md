# Supabase primary broker

Updated November 15, 2026. Supabase is the primary development and onboarding
path. Redis remains a legacy backend with its existing CLI defaults. The
candidate is experimental and unpublished; publication is outside this milestone.

The fresh local Docker workflow passes CLI install/share/invite/join, official
Ollama Python calls and real inference across primary, joined and restarted
workers, including System One, in the earlier acceptance run. The latest reruns
hit Edge CPU limits on the joined worker; see
[the follow-up](progress.md#branch-cleanup-follow-up). Start with the
[walkthrough](../../supabase-walkthrough.md) and
[current evidence](progress.md#fresh-local-cli-and-python-sdk-acceptance).

Hosted Edge has historical real inference, route/stream/private-download,
bounded load/Auth/recovery and size evidence. Its reviewed test function was
removed and its cluster disabled. Reader-disconnect cancellation still fails
through Edge; full fresh hosted wizard acceptance, physical second-machine
onboarding and the broader resource envelope remain open.

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
| [Walkthrough](../../supabase-walkthrough.md) | Fresh Docker setup, Python inference and another Ollama server |
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
