# Optional Supabase backend

Updated October 31, 2026. Status: implemented experimental candidate. Both hosted
profiles passed genuine inference, route/stream/private-download checks, bounded
load/Auth/recovery and size probes. Installed-wheel local CLI onboarding and actual
provider adapter/removal acceptance pass. The approved test gateways are now
removed and their clusters disabled. Both strict reader-disconnect checks still
fail; broader performance/resource evidence and hosted OAuth/button flows remain
incomplete. See [October 14 acceptance](experiments/independent-acceptance.md).
See the [next steps](next-steps.md) and [progress ledger](progress.md).

**Current product priority:** [one-command CLI onboarding](cli-onboarding.md), from
uv to sharing local Ollama, with guided create/use-existing provider setup and a
second-machine join flow. The operator engine is implemented; this complete user
journey is not yet delivered. Hosted OAuth/buttons follow this milestone.
The unpublished `0.0.25a1` candidate's [sharing guide](../../share-ollama.md)
documents create/select Supabase and optional Vercel onboarding without provider
CLIs or database URL input, private second-machine invitations and optional user
services. The [distribution workflow](experiments/distribution.md) verifies exact
artifacts before any explicit publication; fresh/live and published-package
acceptance remain separate evidence gates.

The local lifecycle now includes experimental SQL, worker enrollment, Edge and
optional FastAPI gateways, async results, cleanup and a uv-based demo workflow.
The [progress ledger](progress.md) records current verification and open gates,
including local Edge and both hosted proxy disconnect failures. The
[initial characterization](experiments/step-0-local-characterization.md) retains
the original fixture and test evidence.

## Decisions

- Retain FastAPI + Celery + Redis and its current CLI defaults.
- Make Edge Functions the default gateway **when choosing Supabase**, accepting explicit runtime and resource limits.
- Offer FastAPI with the same Supabase backend on Vercel or a persistent server. Vercel is convenient but also imposes function limits.
- Keep inference on external Ollama workers. Supabase stores jobs, output and worker state; it does not run the models.
- Include async jobs in the initial Supabase release. Slow models are possible; the limiting factor for synchronous use is total request time, including queueing and model loading.
- Target all nine existing routes within each declared gateway envelope. Do not silently replay a request on another backend.
- Prioritize guided CLI share/join with Supabase and optional Vercel; browser installation follows. Cloud deployment, worker readiness and real inference remain separately verified stages within the guided flow.

## Reading order

| Document | What it answers |
| --- | --- |
| [Next steps](next-steps.md) | Ordered remaining work, acceptance criteria, external dependencies and optional manual smoke test |
| [CLI onboarding](cli-onboarding.md) | Adoption-first share/join journeys, provider provisioning gaps and implementation slices |
| [Development handoff](development-handoff.md) | Current candidate status, checkout setup, protocol contracts, hosted prerequisites and evidence requirements |
| [Implementation and feasibility](implementation.md) | Architecture, hosted Edge limits, durable streaming, failures, security, cost and delivery sequence |
| [Installation and hosting](installation.md) | Supabase installer, Vercel Deploy button, hosting profiles, onboarding and upgrade/recovery |
| [Compatibility and verification](compatibility.md) | Existing behavior, known gaps, local evidence and required implementation tests |

## What still prevents qualification?

The architecture is plausible, but hosted measurements must establish a useful Edge workload envelope. The largest risks are forced termination without a client-visible error, the small Edge CPU budget, database write/read amplification for streams, unbounded admission relative to GPU capacity, and mistakes in authorization or lease recovery. Large responses must fit the selected gateway even when stored in Supabase Storage. [Runtime analysis](implementation.md#10-default-edge-gateway-and-asynchronous-jobs), [size policy](implementation.md#size-and-retention-policy).

Installation adds a separate product to maintain: a privileged bootstrap service, versioned deployment artifacts and safe upgrades. An official generic Supabase button that installs this whole application was not established by the reviewed sources; the proposed web flow uses Supabase OAuth and Management APIs. Vercel supplies a Deploy button, but database migrations and worker onboarding remain our responsibility. [Installation evidence and design](installation.md).

Versioned migrations, the CLI, local installer and owned provider adapters are
implemented candidates. Remaining work includes strict disconnect qualification,
fresh hosted/browser onboarding, OAuth/Marketplace/public button setup, and the
broader performance/cost/resource envelope. Do not reuse the retired test
sessions or assume the preserved provider projects are ready for inference. The
[development handoff](development-handoff.md) defines the complete scope; the
[progress ledger](progress.md) identifies the next unverified acceptance gates.

[All plans](../index.md)

[Implementation progress and verification ledger](progress.md)
