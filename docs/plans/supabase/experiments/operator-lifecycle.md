# Versioned operator lifecycle candidate

Updated October 14, 2026. This is an implemented CLI path with local and hosted
Edge evidence, not a production qualification claim. Supabase is the primary
documented workflow; low-level Redis commands retain their defaults.
The persistent local demo keeps using `oshepherd_spike_api`; it is unaffected.

The complete installed-wheel local CLI lifecycle and reviewed hosted removal
acceptance now pass. The hosted qualification cluster is disabled and its reviewed function removed;
the provider project and shared configuration remain.
See [current acceptance](independent-acceptance.md). Old sessions/keys cannot be
reused for hosted inference.

## Release and migration ownership

`supabase/migrations/20261010000100_oshepherd_v1.sql` snapshots the verified
inference protocol into independent `oshepherd`/`oshepherd_api` schemas,
`oshepherd_runtime` owner and `oshepherd-results` private bucket. The second
migration adds server-only installation diagnostics, application-key revocation
and cluster disablement. Existing schema/role/bucket/policy collisions fail.
Never edit an applied migration; upgrades add another ordered migration.

Fresh installations now use `20261011000100_oshepherd_storage_baseline.sql`,
which consolidates those two initial files without their incompatible Storage
path foreign key. Modern Storage supports object versions and no longer has a
unique `(bucket_id,name)` foreign-key target. The October 10 files retain their
original bytes and remain the history of existing installations. Both supported
histories receive the October 11 compatibility and drift migrations. The
manifest declares the permitted paths; mixed, reordered or altered histories
are rejected. The result commit trigger still rejects missing, archived or
deleted objects and locks the metadata row through commit. Its fixed function
uses the installing `postgres` role; runtime metadata reads remain bucket scoped.
The additive `20261011000500` lock fix uses `FOR SHARE`, fencing non-key
version/archive updates as well as physical deletion until commit.
Trigger state and runtime Storage permissions participate in drift detection.
See the [hosted findings](hosted-qualification.md).

The wheel contains SQL, Edge source and a release manifest.
Every artifact and package Python source has a SHA-256. Each SQL file also records its content digest and
the installed catalog fingerprint. The installer checks functions, grants,
owners, columns, constraints, indexes, RLS and policies for drift before replay
or upgrade. Bucket privacy, type and size policy are checked separately.
An unexpected migration/version/hash fails rather than repairing history.

Each migration runs in a transaction with a project lock; schema changes and
both installation and Supabase CLI history commit together. The official CLI's
`supabase db push --local --dry-run` recognizes this history and reports up to
date for the original two-file candidate. The current bundle also contains a
superseding fresh-install baseline: **do not push every SQL file with the provider
CLI**. Use `supabase install`/`upgrade`, which select the manifest's applicable
history and record only files actually applied. Direct manual SQL must follow
that selected history and run inside a transaction.

Schema exposure merges existing global and database-specific role settings,
preserves their order, and reconciles again on upgrades/replay. Unchanged exposure
does not rewrite role settings or trigger another PostgREST reload. For hosted
installs, supply the actual existing exposed-schema list from the project's API
settings; installation refuses to guess it. It checks that unrelated schemas
exist before committing exposure. The implemented Management adapter discovers
the selected project's existing API configuration for the local installer;
real hosted OAuth discovery/onboarding acceptance remains unrun.

## Configure and install

All Python commands use uv. The installed package exposes the same commands
through `oshepherd`; no checkout `scripts` module is required by the operator.

1. Select an isolated **Free** Supabase project. Verify its actual plan before
   provisioning or testing;
   no paid upgrade or larger paid-profile budget is part of this candidate.
2. Create the private connection through hidden prompts:

   ```sh
   uv run --locked oshepherd supabase configure
   ```

   Provide the selected project reference, publishable key, server-only secret
   key, URL-encoded Postgres connection URL and existing API
   schema list. Secrets are not command arguments. Remote database connections
   bind the project reference and require TLS. No gateway receives a DB password
   and no worker receives an administrator key.

   For the existing isolated local stack instead:

   ```sh
   uv run --locked oshepherd supabase configure \
     --local-from .oshepherd/local/connection.json
   ```

   Local configuration uses `supabase_admin` on the isolated port 55322. Its
   `postgres` role cannot grant Storage schema usage. Earlier private connection
   files using local `postgres` need their URL username changed privately to
   `supabase_admin`; the local password stays the same. Hosted configuration
   continues to use the selected project's `postgres`/pooler identity. Install
   checks the Storage schema grant option before modifying migration history;
   insufficient privileges return `oshepherd_database_privileges_required`.
   Cleanup schedule reconciliation migrates the two exact owned local commands
   from `postgres` to `supabase_admin`, avoiding duplicate timers. A conflicting
   command fails before either role's job is removed; unrelated jobs remain.

3. Select an existing, explicitly authorized Supabase Auth owner identity.
   In a fresh project, create that operator identity through the project's Auth
   administration UI first; copy its UUID. The first public visitor/sign-up
   cannot become owner. Bootstrap requires project administrative credentials
   and an explicit owner UUID.
4. Install and inspect:

   ```sh
   uv run --locked oshepherd supabase install \
     --connection-file .oshepherd/operator/connection.json \
     --owner-subject <auth-owner-uuid>
   uv run --locked oshepherd supabase doctor \
     --connection-file .oshepherd/operator/connection.json
   ```

   The private journal stores cluster/key/operation UUIDs before network writes.
   Repeating install preserves identities and keys; a different target, profile
   or owner fails. Concurrent journal writers are rejected. SQL cleanup schedules
   are reconciled only when their exact owned commands match; unrelated jobs
   remain intact. Routine CLI output contains identifiers/counts and error codes.

5. `configured` means SQL, bucket, schedules, ownership and private configuration
   files exist. It does **not** mean a gateway was deployed, a worker is online,
   inference passed or a hosting profile is qualified. Run the independent
   Storage cleanup process on an operator machine/service:

   ```sh
   uv run --locked oshepherd supabase-maintain \
     --env-file .oshepherd/operator/gateway.env
   ```

   Cron bounds SQL expiry/purge and its own execution-log retention; actual
   Storage byte deletion still requires this HTTP runner. Use a service manager
   for continued operation, rather than a serverless post-response task.

## Gateway artifacts

Export a credential-free SQL/Edge bundle into an empty directory:

```sh
uv run --locked oshepherd supabase export-bundle --output .oshepherd/edge-release
```

For an authorized hosted target, first inspect its function names and reject an
unrelated existing `ollama` function. The generated `edge.env` belongs to the
selected operator installation. The separate explicit deployment steps are:

```sh
supabase secrets set --project-ref <selected-ref> \
  --env-file .oshepherd/operator/edge.env
supabase functions deploy ollama --project-ref <selected-ref> \
  --workdir .oshepherd/edge-release --no-verify-jwt --use-api
```

These are explicit operator deployment actions. The function
performs application-key authentication. Its server configuration binds one
cluster. Do not overwrite project-wide Oshepherd secrets for another installation.
The [new provider adapters and local installer](local-installer.md) supplement
these manual commands with ownership receipts and failure reconciliation;
actual Edge adoption/upgrade/reconciliation now pass, while hosted OAuth acceptance
remains pending. A deployed
health response alone does not prove artifact ownership.

## Worker and inference

```sh
uv run --locked oshepherd supabase enroll-worker \
  --connection-file .oshepherd/operator/connection.json --name local --capacity 1
```

Transfer only `local.enrollment.json` and `local.worker.env` securely to the worker
machine, retaining ownership and 0600 permissions. Set `WORKER_SESSION_FILE` to a
private path on that machine and `OLLAMA_BASE_URL` to its actual local server.
Then:

```sh
uv run --locked oshepherd supabase-worker-login \
  --env-file <private-worker.env> --enrollment-file <private-enrollment.json>
uv run --locked oshepherd start-worker --backend supabase \
  --env-file <private-worker.env>
```

Repeated enrollment preserves worker identity/capacity and the challenge. An
expired or ambiguously issued challenge requires explicit `--renew`, which does
not create another worker. A revoked worker requires a new enrollment name.
The worker refreshes and privately saves its own session; administrative project
credentials are absent from these two transfer files. Models are never downloaded
by setup, doctor or enrollment.

After a worker advertises an already installed model:

```sh
uv run --locked oshepherd supabase doctor \
  --connection-file .oshepherd/operator/connection.json
uv run --locked oshepherd supabase verify-inference \
  --connection-file .oshepherd/operator/connection.json --model <installed-model>
```

Doctor separates schema, enabled state, worker/model counts and authenticated gateway
health. It never invokes inference. The explicit check caps generation at eight
tokens and prints only completion/count/size evidence; it does not certify a
provider's limits, costs or cancellation behavior.

## Recovery and disablement

`upgrade`, `rotate-key`, `revoke-worker --name <name>` and `uninstall` use the same
private connection/journal options. Upgrade rejects drift and retains identities
and keys. Rotation creates/verifies the new key before revoking the old one;
lost revocation/config-write acknowledgements resume the same rotation. Finish
a pending rotation with `rotate-key` before invoking install/upgrade.

`uninstall` currently performs cluster disablement: deny admission, cancel active
jobs, revoke application keys and workers, preserve retained data. It keeps the
shared schemas, gateway artifact and cleanup schedules. Physical provider
resource removal is implemented by receipt-bound adapter SDK methods and passes
reviewed hosted acceptance; destructive database purge is outside that operation.
Do not describe `uninstall` as deleting a gateway or the entire provider project.

## Evidence and remaining gates

Real local Auth/SQL tests cover repeated bootstrap, key and Auth creation
acknowledgement loss, rotation recovery, ownership/capacity mismatches, explicit
challenge renewal, denied worker access to administration, revocation, preserved
data and rollback after RLS drift. Production-schema Edge routes, NDJSON, async
results and private downloads also pass. The official CLI recognizes installed
migration history. The Supabase Python gateway factory initializes neither
Redis nor Celery.

The isolated hosted Edge gateway subsequently passed real-model inference, route
fixtures, exact NDJSON and private downloads. Its synchronous deadline was
measured at 135 seconds, and async jobs completed beyond 400 seconds; see the
[hosted report](hosted-qualification.md). The hosted disconnect check fails.

The full current installed-wheel local CLI lifecycle, actual provider preflight/
deployment/reconciliation and reviewed physical removal now pass. Remaining:
fresh hosted/browser onboarding and overlapping-version upgrades, real OAuth
app/callback/host setup, wider performance/resource/cost/quota evidence and the
strict Edge proxy disconnect gate. The [next-steps checklist](../next-steps.md) gives the order and acceptance
criteria; the [progress ledger](../progress.md) owns exact commands and results.
