# Optional Supabase backend

Updated October 7, 2026. Status: experimental implementation in progress. Local
Edge-to-Ollama inference passed; production qualification, hosted benchmarks,
installer and deploy buttons remain pending. See the [progress ledger](progress.md).

The local lifecycle now includes experimental SQL, worker enrollment, Edge and
optional FastAPI gateways, async results, cleanup and a uv-based demo workflow.
The [progress ledger](progress.md) records current verification and open gates,
including the real Edge proxy disconnect failure. The
[initial characterization](experiments/step-0-local-characterization.md) retains
the original fixture and test evidence.

## Decisions

- Retain FastAPI + Celery + Redis and its current CLI defaults.
- Make Edge Functions the default gateway **when choosing Supabase**, accepting explicit runtime and resource limits.
- Offer FastAPI with the same Supabase backend on Vercel or a persistent server. Vercel is convenient but also imposes function limits.
- Keep inference on external Ollama workers. Supabase stores jobs, output and worker state; it does not run the models.
- Include async jobs in the initial Supabase release. Slow models are possible; the limiting factor for synchronous use is total request time, including queueing and model loading.
- Target all nine existing routes within each declared gateway envelope. Do not silently replay a request on another backend.
- Deliver guided Supabase and Vercel installation paths plus CLI recovery. Cloud deployment and worker enrollment are separate steps.

## Reading order

| Document | What it answers |
| --- | --- |
| [Development handoff](development-handoff.md) | First work package, local setup, protocol decisions, hosted prerequisites and evidence requirements |
| [Implementation and feasibility](implementation.md) | Architecture, hosted Edge limits, durable streaming, failures, security, cost and delivery sequence |
| [Installation and hosting](installation.md) | Supabase installer, Vercel Deploy button, hosting profiles, onboarding and upgrade/recovery |
| [Compatibility and verification](compatibility.md) | Existing behavior, known gaps, local evidence and required implementation tests |

## What could invalidate the proposal?

The architecture is plausible, but hosted measurements must establish a useful Edge workload envelope. The largest risks are forced termination without a client-visible error, the small Edge CPU budget, database write/read amplification for streams, unbounded admission relative to GPU capacity, and mistakes in authorization or lease recovery. Large responses must fit the selected gateway even when stored in Supabase Storage. [Runtime analysis](implementation.md#10-default-edge-gateway-and-asynchronous-jobs), [size policy](implementation.md#size-and-retention-policy).

Installation adds a separate product to maintain: a privileged bootstrap service, versioned deployment artifacts and safe upgrades. An official generic Supabase button that installs this whole application was not established by the reviewed sources; the proposed web flow uses Supabase OAuth and Management APIs. Vercel supplies a Deploy button, but database migrations and worker onboarding remain our responsibility. [Installation evidence and design](installation.md).

Remaining work includes Edge disconnect behavior, production migrations,
installer/operator flows and hosted/load qualification. The
[development handoff](development-handoff.md) defines the complete scope; the
[progress ledger](progress.md) identifies the next unverified acceptance gates.

[All plans](../index.md)

[Implementation progress and verification ledger](progress.md)
