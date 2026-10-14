# Local installer and provider adapters

Updated October 14, 2026. Candidate implementation; hosted OAuth/button onboarding is
**not run**. No OAuth app/callback/hosting exists yet; the user chose local
implementation and validation first. Both gateways remain experimental under
the existing disconnect contract.

Actual Edge/Vercel adapter acceptance and reviewed live removal now pass; both
test gateways are removed and their clusters disabled. See
[independent acceptance](independent-acceptance.md) before reproducing hosted tests.

The installer is a separate, single-operator FastAPI control service. The browser
queues durable requests; a persistent runner uses the existing `Operator.install`
and transactional psql migrations. It needs a POSIX host, private durable
filesystem, Python 3.12, uv and psql. It is not an inference-gateway background
task or a public multi-user installation portal.

## Validate locally without provider setup

```sh
uv sync --locked --extra supabase --extra installer
uv run --locked --extra installer pytest -q \
  tests/contracts/test_supabase_installer.py \
  tests/contracts/test_supabase_edge_deployment.py \
  tests/contracts/test_supabase_vercel_deployment.py
```

Tests use actual ASGI requests/private journals and mocked official provider
HTTP/CLI responses. They cover login/CSRF, S256 PKCE, session-bound one-use state,
denied/expired/replayed callbacks, ambiguous exchanges, project binding, browser
and service restart, stable shared-engine operation IDs, worker-download isolation,
deployment recovery and exact owned-resource removal. Hosted consent, deployment
and first-inference acceptance remain distinct.

Management tokens, PKCE verifiers and browser sessions are Fernet encrypted with
record identity and expiry. The default resume window is one hour; the runner
purges expired records. Keep the 0600 configuration/encryption key outside vault
backups. CLI connection files/operator journals retain protected project
credentials; deleting temporary OAuth access does not delete these or stop the
installed runtime. Worker downloads contain only publishable configuration and
the project-bound one-use challenge, with a portable relative session path.

## Run after registering the OAuth app

Register the exact `<fixed-origin>/oauth/callback` and the required project,
API-key, REST-configuration, Edge-function and secret capabilities. Scopes are
configured on the app. SQL uses psql and a privately supplied Postgres URL;
existing passwords cannot be retrieved through OAuth. [Supabase integration guide](https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration)

```sh
uv run --locked --extra installer oshepherd supabase installer-init
# Hidden secret prompts and explicit project/organization allowlist.
uv run --locked --extra installer oshepherd supabase installer-serve \
  --config-file .oshepherd/installer-config.json
# Separate persistent process; closing the browser does not discard setup.
uv run --locked --extra installer oshepherd supabase installer-work \
  --config-file .oshepherd/installer-config.json
```

Open `http://127.0.0.1:55430` in development. Hosting needs controlled HTTPS and a
trusted local reverse proxy preserving Host/scheme. Disable callback/query logs
in that proxy; the CLI disables Uvicorn access logging. Sign in, connect Supabase,
select an allowlisted project and supply its existing Auth owner UUID and private
Postgres URL. Reopening the page discovers durable installations. Resume keeps
the same identities. The default runner deploys Edge; conflicting resources are
refused. Readiness separates cloud readiness, worker connection and explicit
bounded inference verification.

Create/download worker files, transfer them privately, set both to mode 0600,
then follow the [worker login/start runbook](operator-lifecycle.md). The installer
does not install Ollama or download a model.

## Provider CLI and recovery

```sh
uv run --locked oshepherd supabase deploy-edge \
  --connection-file <private-connection.json> --state-dir <owned-operator-dir> \
  --provider-file <private-supabase-provider.json>
uv run --locked oshepherd supabase deploy-vercel \
  --connection-file <private-connection.json> --state-dir <owned-operator-dir> \
  --provider-file <private-vercel-provider.json> --wheel <verified-candidate.whl>
```

Edge provider JSON has `organization_id` and `access_token`; Vercel JSON has
`team_id`, `project_id`, `access_token`. Create them privately with mode 0600;
never use token-valued command arguments or paste secrets into chat.

Edge bundles verified packaged source without deployment, records the provider
hash, rejects function/secret collisions and reconciles lost acknowledgements.
Explicit `--adopt-existing` requires exact artifact, cluster-secret and health
proof. Anonymous denial and authenticated health are checked separately.
[Function deployment API](https://supabase.com/docs/reference/api/v1-deploy-a-function)

Vercel verifies actual Hobby billing, project account binding and the verified
gateway domain. A fresh verified wheel bundle receives eight encrypted,
Production-only values through stdin. The CLI auth file stays outside uploads;
exact team/project IDs override ambient bindings. Preview/shared-target collisions
are refused; unrelated variables remain. Saved operation metadata reconciles an
ambiguous deployment; a missing match requires investigation rather than a blind
duplicate. READY and stable-domain authentication are independent checks.
The approved isolated Hobby target passed actual preflight, encrypted Production
configuration, a clean-wheel deployment, independent health/denial verification
and a repeated call reusing the same deployment. Edge's Management adapter now
passes actual exact CLI artifact adoption, upgrade and repeated reconciliation.
Its source-relative upload paths match the official CLI's bundle hash; ownership
checks remain strict. [Vercel CLI](https://vercel.com/docs/cli/deploy)

`plan-edge-removal` prints an owned-resource plan without mutation. Both adapters
expose `removal_plan()` and `remove(expected_*_id, operator)` for a reviewed exact
resource. Admission is disabled first; only the receipt-bound Edge function or
Vercel deployment is removed. Shared schedules, data, environments/secrets,
other deployments and provider projects remain. Existing CLI `uninstall` remains
cluster disablement. Reviewed hosted removal tests now pass on both providers,
including recovery after simulated acknowledgement loss following actual
deletion, repeated removal, and independent preservation checks. The
[removal harness](independent-acceptance.md#actual-provider-adapter-and-removal-acceptance)
defaults to read-only planning; execution requires its saved reviewed plan.
Serialize provider administration during removal: provider read/delete calls are
not a cross-service transaction.

## Remaining acceptance

Real consent/refresh/revocation, HTTPS callbacks, fresh hosted/browser onboarding
and a published pinned template/button remain unrun. The current packaged CLI
browserless-worker lifecycle passes locally; it does not establish hosted browser
acceptance. Marketplace access and
installer hosting/service ownership are still prerequisites. These local tests
do not close hosted V16–V21 or justify publishing a placeholder button.
