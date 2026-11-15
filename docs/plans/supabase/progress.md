# Supabase progress ledger

Updated November 15, 2026. This ledger summarizes the Supabase evidence retained
on this branch. Supabase is now the primary documented broker; Redis remains
available with its existing CLI defaults. The candidate is unpublished and not
production-qualified. PyPI publication is outside the current milestone.

## Current implementation

| Area | Delivered | Remaining |
| --- | --- | --- |
| Broker | Postgres jobs, leases/generations, bounded output, Auth worker identities, private Storage results and cleanup | Wider cost/resource qualification |
| Gateway | Edge routes for the existing Redis endpoint set; optional persistent FastAPI with Supabase | Reliable reader-disconnect cancellation through Edge |
| Local setup | Fresh Docker stack, CLI configure/install/create-owner, repeat installation and real SDK inference | Physical second-machine networking |
| Worker onboarding | `share`, private `invite`, `join`, session refresh/restart, capability discovery | Full fresh hosted-project and physical second-machine acceptance |
| Hosted setup | Organization/project create/select, Management SQL, owned Edge deployment and recovery | Latest complete wizard against a fresh hosted project |
| Operations | Doctor, migrations, rotation, worker revocation, disablement, maintenance, user services | Full hosted upgrade/resource matrix |
| Packaging | Hash-verified wheel assets and local candidate verification | Public distribution deferred |
| Browser installer | Local OAuth/control service and fault tests | Hosted OAuth/callback/installer acceptance |

## Fresh local CLI and Python SDK acceptance

On November 15, the local harness started new uniquely named Docker Supabase
projects with empty databases. It copied gateway sources only, then installed
all schema through `oshepherd supabase install --create-owner`. Repeat install
preserved identity and keys. The runner used the real Edge/Auth/SQL/Storage path
and the official Ollama Python package against actual local Ollama.

The first worker passed generate/chat JSON and streaming, legacy embeddings,
model inventory/show/ps, version and health. A second actual Ollama server joined
through a private invitation. The first worker stopped, SQL confirmed work was
owned by the joined worker, and checks passed again after its saved-session
restart. Joined state did not contain operator credentials.

The original local run had 12 checks per stage. Following an explicitly
authorized refresh of `tev1:4b`, actual System One inference passed directly and
through Supabase: **13 checks per stage**, across primary, joined and restarted
workers. The refreshed model can return a valid typed decision with zero output
tokens. `nimble:latest` was not refreshed and is not part of this success claim.
Manual `--serve` mode also passed; its own stack and processes were cleaned up.

These were two servers on one host using shared model files. The subprocesses
ran outside the checkout using its editable installation. This is neither
physical second-machine acceptance nor published-package acceptance.

Private ignored reports are under `.oshepherd/local/`:

- `acceptance-20261115-c/report.json`: original local endpoint run.
- `acceptance-20261115-manual/report.json`: manual serving and cleanup.
- `acceptance-20261115-systemone/report.json`: real System One, 13 checks per stage.
- `systemone-refresh-direct-20261115.json`: direct refreshed-model check.

The implementation supports the SDK's legacy embeddings `prompt` alias while
retaining canonical `input` and its precedence. Workers advertise System One
when a model declares `decision`. Local connection import binds the selected
loopback ports and local administrator; Edge uses the containers' injected
internal URL/key. These fixes do not establish new hosted administrator privileges.

See [local acceptance](../../setup-supabase-local.md) and the
[walkthrough](../../supabase-walkthrough.md).

## Branch cleanup follow-up

The Supabase-focused branch passes **545 offline Python tests** and a clean
source archive/wheel build, hash verification and isolated installed CLI checks.
The manifest contains only `edge_free`, still `qualified: false`. Shared runtime
pins moved to `requirements/runtime.txt` and match the locked export.

A fresh real-model rerun at `.oshepherd/local/supabase-only-acceptance/report.json`
passed installation and the first worker, including System One. On the joined
worker, the System One request returned HTTP 503. The Edge log explicitly records
CPU soft/hard limits and supervisor cancellation of `/v1/systemone`. The runner
recorded failure and successfully cleaned up its stack. No Edge source, SQL
migration or inference worker code changed in this branch cleanup. This failed
rerun remains separate from the earlier 13-check passes; the broader runtime
resource envelope is still unqualified.

A second fresh rerun without System One,
`.oshepherd/local/supabase-only-standard-acceptance/report.json`, again passed
installation and the first worker, then failed on joined-worker generate with
HTTP 546 `WORKER_LIMIT`. Its Edge log records the same CPU soft/hard limits and
supervisor cancellation. Cleanup passed. Neither latest run reached the
joined-worker restart stage. The repeated CPU limit is an open local runtime
qualification issue, not a successful full smoke test for this revision.

## Local-first validation of candidate r3

The October 31 candidate added guided creation/selection, Free verification or
explicit fallback attestation, Management SQL without a private database URL or
`psql`, and per-user services. Local testing covered real macOS launchd and
containerized Linux systemd service lifecycles and fresh-stack Management SQL.
Services require a verified installed package rather than an editable checkout.

The Management transport reuses transactional migrations, locks, immutable
history and drift checks. Local tests cover interrupted creation reconciliation,
owner/bootstrap recovery and credential redaction. They do not replace a live
fresh-project wizard run. See [CLI onboarding](cli-onboarding.md) and
[its evidence](experiments/cli-onboarding.md).

## Earlier Supabase acceptance

The October hosted work used an explicitly selected isolated Free project.
Real Ollama inference, route fixtures, exact NDJSON, authenticated metadata and
private signed downloads passed. The synchronous application deadline was
measured near 135 seconds; accepted async work completed after more than 400
seconds. These measure application behavior, not every platform ceiling.

Independent checks also covered 1,000 jobs, actual Auth expiry/refresh and saved
sessions, generation fencing after restart, no retry after durable output,
incomplete EOF, explicit cancellation, silence/heartbeat behavior, expiry and
maintenance. Request/output boundaries and a paused reader passed after a
bounded upload-drain fix. Exact Edge measurements and limitations remain in
[hosted qualification](experiments/hosted-qualification.md) and
[independent acceptance](experiments/independent-acceptance.md).

The actual Edge adapter passed adoption, upgrade, authenticated health and repeat
reconciliation. Reviewed removal passed with simulated loss of the acknowledgement
after a real provider DELETE. The cluster is disabled and its gateway removed;
retained data, shared schedules and project-wide secrets were preserved. Those
retired credentials and receipts must not be reused blindly.

The installed-wheel local lifecycle passed configure, repeat install/upgrade,
enrollment/login, API/worker startup, doctor, real JSON/NDJSON, worker restart,
key rotation, revocation and repeated disablement. That run used a new cluster
in an existing local project; the November harness separately proved a fresh
local database.

## Verification history

Counts are snapshots of their respective source revisions, not cumulative totals.

| Date/scope | Evidence |
| --- | --- |
| October 14 deterministic/local combined selection | 429 passes; one known strict Edge disconnect failure; two model cases deselected |
| October 14 installed-wheel lifecycle and owned Edge removal | Passed; see independent acceptance |
| November 15 local CLI/SDK implementation | 573 offline Python passes; 50 Deno passes; three selected real Edge route/SDK integration passes |
| November 15 real-model fresh-stack acceptance | 13 checks per stage with System One; joined-worker routing and restart verified; cleanup passed |
| November 15 Supabase-focused branch cleanup | 545 offline Python tests pass after removing the separate hosting adapter's tests; Edge and Redis contract coverage retained |

## Open qualification gates

Reader-disconnect cancellation is still failing through local and hosted Edge.
The hosted signal probe observed the abort only after stream completion; SQL
correctly refused to overwrite the completed terminal state. Keep the strict
regression and `qualified: false`. The persistent Python gateway's working
socket cancellation is separate evidence.

Fresh hosted wizard acceptance, physical second-machine onboarding, broader
performance/resource/cost bounds, complete upgrade scenarios and hosted OAuth
remain open. API expansion beyond the existing Redis endpoint set and public
publication are deferred. Follow [next steps](next-steps.md).
