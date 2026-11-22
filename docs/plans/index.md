# Plans

| Plan | Scope | Status |
| --- | --- | --- |
| [Supabase primary broker](supabase/index.md) | Edge gateway, optional FastAPI gateway, workers, clients, installation and operations | Implemented experimental candidate; remaining qualification tracked in the progress ledger |

These documents contain design decisions and acceptance criteria. Start with the
[documentation index](../README.md) for user guides. Supabase is the primary
onboarding path; existing FastAPI + Celery + Redis remains supported, and
`start-api` / `start-worker` retain their Redis defaults.
