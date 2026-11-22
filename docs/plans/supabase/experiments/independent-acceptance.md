# October 14 independent acceptance

> Dated evidence. Results and feature gaps below describe the recorded revision.
> See the [progress ledger](../progress.md) for current status and the
> [documentation index](../../../README.md) for current user instructions.

Recorded October 14, 2026; this page retains Supabase Edge evidence. The
user preserved the gateway contract and experimental status and approved
removal of the isolated qualification function. The cluster is disabled and the
function removed; the project, data and shared configuration remain. Old worker
sessions and application keys cannot be reused for inference.

## Hosted size and slow-reader evidence

The new `scripts.supabase_hosted_check --envelope` uses the real gateway,
SQL/Auth/Storage and a loopback fixture worker. It requires an idle configured
installation, refuses retired states, keeps the existing limits, bounds its
workload to 600 seconds and cancels only its own unfinished jobs during cleanup.
Reports are private; client pauses are not claimed as observed server backpressure.

| Workload | Edge Free |
| --- | --- |
| Request bodies: 65,536; 262,144; 1,047,552 bytes | Exact completion, 200 |
| Request: 1,048,577 bytes | Application 413; no job admitted |
| Request: 5,242,880 bytes | Application 413 in 0.468 s after fix; no admission |
| JSON output: 65,536 and 262,144 bytes | Exact bytes |
| JSON at response budget | Exact 1,048,576 bytes |
| JSON one byte over response budget | 413 `result_too_large`; SQL succeeded, retained result |
| Slow NDJSON reader, five-second pause | Exact 819,224 bytes in 10.242 s |
| Metadata during reader pause | 0.314 s |

Initial Edge five-MiB requests timed out at 60 seconds. Awaiting request-body
cancellation could block rejection; returning before consuming the upload also
did not work through the hosted proxy. The final handler discards a finite upload
tail before returning 413: an eight-MiB discard budget checked between chunks and
a five-second deadline, without retaining discarded chunks. Cancellation is
initiated without waiting for its promise to settle. The accepted request budget
remains one MiB; the SQL normalized-envelope budget applies separately. This does
not change inference reader-disconnect semantics. Deno regressions cover pending
cancellation, finite EOF draining, drain time and byte budgets. Earlier failed
private reports remain separate from the successful final probe.

These measurements do not establish hosted gateway memory, sustained server
backpressure, Redis-relative latency, provider invocation ceilings or remaining
account quota. A near-limit successful request is not a promise that every raw
one-MiB payload fits its normalized SQL envelope.

## Installed-wheel CLI lifecycle

`scripts/supabase_packaged_check.py` runs from an isolated uv-installed current
wheel and clean working directory, with no checkout imports or Redis service.
It creates a new cluster in the existing isolated local project, rather than
resetting the user's stack or claiming a new empty hosted project.

Actual configure, install/repeated install, upgrade, repeated enrollment,
browserless worker login, API/worker CLI startup, doctor, bounded real JSON and
terminal NDJSON inference, worker restart with SQL generation advancement, key
rotation, revocation and repeated cluster disablement pass. The already installed
`llama3.2:3b` is used; no model is downloaded. Processes stop, qualification
clusters remain disabled, and protected journals remain for inspection. Full
fresh hosted browser/project onboarding remains a separate V16–V21 gate.

Reproduce from a clean directory with the actual wheel version and fresh private
work directory; keep the existing isolated local stack and Ollama running:

```sh
uv run --isolated --no-project --python 3.12 \
  --with '<absolute-current-wheel.whl>[supabase,installer]' \
  --with-requirements <absolute-repo>/requirements/runtime.txt \
  python <absolute-repo>/scripts/supabase_packaged_check.py \
  --local-from <absolute-repo>/.oshepherd/local/connection.json \
  --work-dir <fresh-private-directory> --report-file <private-report.json> \
  --model llama3.2:3b
```

## Actual provider adapter and removal acceptance

Strict Edge adoption initially failed because the adapter flattened filenames.
Provider bundle-only comparisons established that source-relative module paths
produce the official CLI's exact bundle hash. Uploads now use
`supabase/functions/ollama/*` and the matching entrypoint. Adoption still requires
the exact artifact, secret digests and authenticated health. Actual adoption,
Management API upgrade to the final request-reader fix, independent health and
repeat reconciliation pass on the same function ID.
[Deployment API](https://supabase.com/docs/reference/api/v1-deploy-a-function)

The user reviewed the exact ownership plan and approved deleting the test Edge
function. The removal harness requires the saved plan, matching receipt and zero
active jobs. After a real provider DELETE, it deliberately discarded the
acknowledgement and recovered through provider reads. Removal and a repeated
call passed.

The before/after retained job count was **1,059**, with identical job-ID digests,
schema fingerprints and shared Cron commands. The cluster was disabled with zero
active jobs. Independent reads confirmed the function absent and five owned
secret digests preserved. No project deletion, database purge or paid upgrade
occurred. Private plans/reports remain ignored and mode 0600.

Read-only planning and reviewed execution use separate invocations:

```sh
uv run --locked python -m scripts.supabase_hosted_removal \
  --provider edge --connection-file <private-connection.json> \
  --state-dir <owned-operator-directory> --provider-file <private-provider.json> \
  --plan-file <private-reviewed-plan.json> --report-file <private-report.json>
# Review that exact plan before adding --execute; --simulate-ack-loss is opt-in.
```

Reproduction needs new explicitly selected disposable installations. Do not use
the retired test sessions, overwrite the preserved project-wide Edge secrets
for a new cluster, or reuse removed ownership receipts blindly.

## Verification and remaining gates

The final combined deterministic/local selection has **429 passes, one known
strict Edge disconnect failure and two opt-in model cases deselected** in
122.79 seconds. An earlier run had 422 passes and two failures: that strict gate
plus an initial route-health 503. The route case then passed alone and in the
full rerun; its transient cause remains unestablished. Subsequent probe cleanup
and retired-state guard cases are included in the current offline suite: **363
passes in 14.07 seconds**. Deno
has **48 passes**, and the entrypoint type check passes. Clean source-archive/
wheel builds and the current installed-wheel lifecycle pass.

The final strict hosted Edge route/NDJSON/private-download probe passed its
route checks and still failed disconnect cancellation: SQL became `succeeded`.
The download fixture was 1,200,347 bytes. Keep `edge_free` at `qualified: false`.

Hosted OAuth consent/refresh/revocation, a controlled HTTPS callback/installer
host, public distribution and browser installation, and complete
fresh hosted onboarding remain unrun. Wider performance/cost/quota and server
resource evidence remains incomplete. The user explicitly chose local-first
work while OAuth prerequisites are absent; there is no placeholder public button
or alternative cancellation protocol. See [next steps](../next-steps.md).
