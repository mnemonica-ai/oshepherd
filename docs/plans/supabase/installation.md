# Supabase installation and hosting

Updated November 15, 2026. The guided CLI and operator engine are implemented;
fresh local Docker acceptance passes. The latest full wizard against a newly
created hosted project and reliable Edge disconnect cancellation remain open.
See the [walkthrough](../../supabase-walkthrough.md),
[hosted guide](../../setup-supabase.md) and [progress ledger](progress.md).

## Product path

Supabase is the primary broker. Edge hosts the Ollama-compatible gateway;
external workers connect outward and execute models in Ollama. Redis remains a
legacy option with its existing CLI defaults. No Redis service is required for
the Supabase workflow.

`oshepherd share` creates or selects an organization/project, obtains private
provider credentials, checks the selected Free plan, installs the schema through
the Management API, deploys Edge, enrolls a worker and verifies real inference.
Cloud readiness, worker readiness and successful inference are separate events.
Setup does not download models. A durable private journal reconciles ambiguous
creation results and prevents silent target changes.

For local Docker, configure from a private `supabase status -o json` export,
run `install --create-owner`, serve the function locally, then pass the prepared
connection to `share`. This uses local database administration rather than the
hosted Management API. Each installation keeps its own private state directory.

## Hosting profiles

The operator manifest supports `edge_free`. The optional Python gateway uses
`OSHEPHERD_GATEWAY_PROFILE=persistent`; it still connects to the Supabase broker.
The current candidate's SQL maximum is 135 seconds, including waiting/loading.
Longer synchronous persistent budgets are a future target, not implemented support.

| Policy | Current candidate |
| --- | --- |
| Synchronous time | 135 seconds |
| Input body | 1 MiB |
| Edge synchronous response | 1 MiB, including streamed bytes |
| Inline result threshold | 256 KiB |
| Total async retained output | 32 MiB |
| Async retrieval | Explicit `/jobs` status/result/download flow |

Storage does not bypass a response that still crosses Edge. Ordinary Ollama
routes return their normal response or an explicit limit error, not a silent
redirect to a download URL. Provider termination can still cause abrupt EOF;
application limits are deliberately conservative. The broader hosted resource
and quota envelope remains unqualified.

## Shared bootstrap engine and recovery

The packaged manifest describes SQL hashes and permitted migration histories,
Edge artifacts, Python sources, schema/protocol versions and owned schedules.
CLI and local OAuth installer consume the same engine. Never edit an applied
migration or blindly push every baseline and legacy migration together.

| Stage | Durable evidence |
| --- | --- |
| Select target | Private organization/project/profile binding |
| Create resources | Journaled intent; reconcile uncertain responses before retry |
| Bootstrap owner | Recorded Auth identity, repeatable recovery |
| Apply schema | Transactional project lock, selected immutable history and drift checks |
| Configure installation | Stable cluster/key identities and private client/gateway files |
| Deploy Edge | Owned function receipt, artifact/secret checks and authenticated health |
| Enroll worker | One-use challenge and restricted saved Auth session |
| Verify inference | Actual request completed by connected Ollama |

Existing schema exposure is merged, not replaced. Schema, bucket and function
collisions must fail safely. Configuration and worker/session secrets remain in
owned private files; no secrets belong in command arguments, URLs or logs.

The [Management SQL endpoint](https://supabase.com/docs/reference/api/v1-run-a-query)
is an experimental transport isolated behind an adapter. Retain the direct
operator database path and verify live error/response shapes before claiming
fresh hosted acceptance. The
[function deployment API](https://supabase.com/docs/reference/api/v1-deploy-a-function)
is used by the owned Edge adapter.

## CLI and operations

```sh
oshepherd share
oshepherd invite --name second-machine --output /private/second.invitation.json
# On the joined machine, with Ollama running:
oshepherd join --invitation-file /private/second.invitation.json
# Later:
oshepherd join
```

The invitation expires, is single-use and contains no operator credentials.
`share` supervises a worker and stored-result cleanup; keep it or its user service
running. For multiple workers/installations, use separate state directories.
See the [operator runbook](experiments/operator-lifecycle.md) for manual
configure/install/upgrade, diagnostics, rotation, revocation and recovery.

`supabase uninstall` disables the cluster and revokes its credentials while
preserving schema/data. Reviewed owned-function removal is a separate operation.
Provider project deletion is not implicit in either command. Backups, region,
prompt/result retention and quota monitoring remain the operator's responsibility.
Upgrades must preserve identities and overlapping protocol compatibility.

## Later browser installation

A local OAuth control service and fault tests are implemented. Hosted acceptance
still needs a registered Supabase OAuth app, controlled HTTPS callback domain,
durable private credential store and maintenance owner. Real consent, refresh,
revocation, browser restart and queued-install recovery remain unverified. There
is no public installation button. CLI and browser must eventually complete the
same owned installation and prove actual inference independently of readiness.

Keep public distribution deferred and the candidate experimental. The remaining
V16–V21 installation gates in the [matrix](compatibility.md) retain their IDs;
the retired hosting-specific gate is marked out of scope. Follow
[next steps](next-steps.md) before claiming complete hosted installation.
