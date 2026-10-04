# Supabase installation and hosting

Updated October 4, 2026. Companion to the [implementation plan](implementation.md) and [verification matrix](compatibility.md). All Oshepherd installer commands, templates and buttons below are **proposed deliverables**, not available features.

## Installation promise

Provide two guided entry points: **Install on Supabase** for the default Edge gateway, and **Deploy with Vercel** for optional FastAPI + Supabase. Both use the same schema, credentials, worker protocol and bootstrap manifest. Provide a CLI path for operators who do not want a hosted installer or need to recover a failed setup.

“One click” means one entry point followed by account consent, project/region selection and provisioning. It cannot mean no accounts, billing choices or worker setup. Neither platform button installs Ollama, downloads models, grants hardware access or supplies a GPU. The success screen must distinguish **cloud ready**, **worker connected**, and **inference verified**. A deployment without a worker is a valid intermediate state, not a working inference service.

## Hosting profiles

Backend selection and hosting policy are different settings. Proposed `OSHEPHERD_GATEWAY_PROFILE=edge|vercel|persistent` determines admissible synchronous time and bytes. The installer configures the actual platform and matching application limits together; do not trust a client-supplied profile or silently assume the paid tier. Workers enforce the resulting absolute job deadline and byte budget.

| Profile | Documented hosting boundary | Initial application policy |
| --- | --- | --- |
| Supabase Edge Free | 150 s worker lifetime; 150 s request idle timeout | Candidate 135 s synchronous budget, reduced by setup/elapsed time; validate reused isolates |
| Supabase Edge paid | 400 s worker lifetime; same 150 s idle timeout | Candidate 135 s non-streamed / 385 s streamed; long silence still matters |
| FastAPI on Vercel Hobby, Fluid compute | Python invocation maximum 300 s | Configure 300 s explicitly; candidate application budget 285 s |
| FastAPI on Vercel Pro/Enterprise, Fluid compute | Default 300 s; generally available maximum 800 s | Only use candidate 785 s application budget when deployment explicitly configures 800 s |
| FastAPI on persistent host | No Supabase Edge invocation clock | Operator-defined deadline, validated through the actual ingress proxy and client |

Supabase also limits Edge memory to 256 MB and CPU to 2 s per request excluding asynchronous I/O. These constrain relay parsing/buffering even though Ollama runs elsewhere. Application budgets are test candidates, not guaranteed remaining worker lifetimes. The [Edge analysis](implementation.md#10-default-edge-gateway-and-asynchronous-jobs) defines cutoff behavior. [Supabase limits](https://supabase.com/docs/guides/functions/limits)

Vercel duration includes response transmission and streaming. Current documentation also offers a **1,800 s beta** maximum for eligible Pro/Enterprise runtimes, including Python 3.12–3.14, with function-level configuration and exclusions for Secure Compute/Static IPs. Keep this an opt-in experiment; do not base the release promise on beta access. Network/client idle timeouts remain separate. These are Python Function limits, not Vercel Edge runtime limits. [Vercel duration](https://vercel.com/docs/functions/configuring-functions/duration)

Vercel documents a **4.5 MB request or response payload limit**. Start with the shared 1 MiB input limit and a conservative 4,000,000-byte synchronous response cap, including NDJSON bytes. Test buffered JSON and Python streaming just below/above that boundary; do not assume a streaming exemption. A Storage-backed response still crosses the gateway. Large async results use explicitly requested private signed downloads; ordinary Ollama routes must not silently return URLs. [Vercel limits](https://vercel.com/docs/functions/limitations)

Published limits must come from hosted tests of each profile and plan. Default application limits may be lower than platform maxima. Where output size is unknown at admission, enforce growth in the worker and fail explicitly. A provider rejecting before the handler or killing it mid-response may produce its own error or abrupt EOF; document that exception to application error mapping.

## Default path: Install on Supabase

The reviewed official sources support a custom OAuth integration with a Connect Supabase button. They do not establish a generic application-template button that installs Oshepherd SQL, policies, secrets and functions together. We must build and operate that flow, with a registered OAuth app and server-side callback. Use state validation, PKCE S256 and a fixed callback allowlist; keep the OAuth client secret and token exchange on the installer server. [Supabase integration guide](https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration)

Proposed journey:

1. Open the installer, authenticate the operator, and connect Supabase. Explain access and show the release being installed.
2. Select an authorized organization and project, or create a dedicated project where the API/account supports it. Display region, plan, expected resource changes and potential charges before provisioning. Never silently upgrade a plan.
3. Check project health, permissions, existing installation, schema/function name collisions and supported versions. Recommend a dedicated project; do not overwrite unrelated resources in an existing one.
4. Run the shared bootstrap stages below, reporting progress and recoverable errors. Configure the default Edge gateway with custom application authentication before exposing inference.
5. Bind the initial Oshepherd operator to the authenticated installation session, create a cluster and application key, and present worker enrollment. Public signup or the first anonymous visitor cannot become owner.
6. Show the gateway host, measured profile limits, client configuration, readiness and next worker step. Offer a bounded inference check only after a worker is online and the operator chooses a model.

The hosted installer is a separate control service: it cannot depend on first installing itself into the customer's target project. It needs durable installation state, protected credentials, an operator support path and ongoing maintenance. Do not require it for normal inference after setup. Deleting its retained provider tokens must not stop an installed cluster.

Request only the OAuth capabilities needed for selected project discovery, SQL migrations, function deployment, API configuration and secrets; project creation requires additional authority. Scopes are configured on the OAuth app. A consent grant can cover more resources than the selected target, so enforce the selected organization/project on every installer action. Management OAuth is separate from the Supabase Auth session used by an inference worker. [OAuth scopes](https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration/oauth-scopes)

Deploy the Edge artifact using the [Management function deployment API](https://supabase.com/docs/reference/api/v1-deploy-a-function). The [SQL query endpoint](https://supabase.com/docs/reference/api/v1-run-a-query) is explicitly experimental/Beta: isolate it behind a tested adapter and retain the CLI migration path. Do not describe browser-only SQL provisioning as a stable platform guarantee. The initial capability probe must verify all required operations, especially project configuration and access to Marketplace-owned projects; unresolved API access gets an explicit CLI/manual handoff, not a fake successful installation.

## Optional path: Deploy with Vercel + Supabase

Vercel's official Deploy button clones a public template and creates a project. It supports required environment variables, non-secret defaults, integration selection and a post-deployment redirect. Publish a real button only once its template, integration identifiers and return flow have been exercised. Never embed keys, enrollment secrets or management tokens in its URL. [Deploy Button](https://vercel.com/docs/deploy-button)

Provide two database choices:

| Choice | Setup | Constraint |
| --- | --- | --- |
| Existing Supabase project | Run/connect the shared installer, then deploy the Vercel template with server-only configuration | Verify the selected project and schema version; do not migrate during a Vercel build |
| Supabase through Vercel Marketplace | Create/connect the resource through Vercel, then run the same Oshepherd bootstrap on it | Validate integration access before promising a seamless return flow; fall back to CLI setup |

Supabase's Marketplace guide describes automatic environment synchronization and unified billing, but labels the integration **Public Alpha** and says project creation is through the Vercel dashboard. Do not assume the installer can create projects in those organizations through the ordinary Management API. Synced variables include `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SECRET_KEY`; unrelated database credentials must not be required by the HTTP/RPC gateway. [Marketplace guide](https://supabase.com/docs/guides/integrations/vercel-marketplace)

The template must explicitly configure `OSHEPHERD_BACKEND=supabase`, the Vercel hosting profile, cluster, application-key auth and matching function/application deadlines. Verify the integration's actual variable names and scopes; support a documented manual server-variable path. Never map privileged secrets into `NEXT_PUBLIC_*` or client bundles. An installer redirect or a user-supplied deployment URL is not proof of ownership: verify any Vercel project binding through authenticated provider access before writing secrets. Without that integration, let the owner enter them in Vercel's protected configuration UI.

Export an ASGI `app` from a minimal entrypoint using a backend-aware factory; do not launch the existing Uvicorn CLI, register process signal handlers, construct Celery, or require Redis at import. Vercel packages FastAPI as one Python Function and supports lifespan events. Its shutdown cleanup window is only 500 ms after SIGTERM, so cleanup cannot depend on a graceful long drain. Configure `maxDuration` against the resolved entrypoint filename. [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)

Use per-request cursor readers initially. Reuse HTTP clients within a live instance, but assume the process can disappear. No migration, permanent collector, lease reaper or essential background task may depend on its lifetime after a response. Database leases and scheduled cleanup remain authoritative. Keep Ollama and model files outside the function bundle; pin the released Python package and supported runtime.

Production needs a stable gateway URL that ordinary Ollama clients can access using application auth. Include deployment-protection/interstitial behavior in hosted verification; a browser login page is not an API response. Apply private/no-store caching to inference, async status/results and authorized metadata. Preview deployments default to inference disabled and receive no production gateway secrets. Enabling a preview requires an isolated project/cluster, credentials and worker pool; a separate URL alone does not isolate a queue.

## Shared bootstrap engine and recovery

One versioned release manifest describes SQL hashes/order, protocol range, Edge artifact, Python version, required configuration, owned resource names and cleanup schedules. The web installer and CLI consume the same manifest. Do not maintain independent migrations in a Vercel template. Make published templates self-contained by depending on a released package and manifest, not on unpublished files outside the template root.

| Stage | Durable completion evidence |
| --- | --- |
| Authorized | Operator session, selected provider/project IDs and granted capabilities verified |
| Project ready | Project services reachable; preflight configuration and collisions checked |
| Schema ready | Exact migration hashes recorded; RPC grants/RLS/protocol verified; API schema exposure merged safely |
| Services ready | Private result bucket/policies, bounded Cron cleanup and namespaced configuration present |
| Ownership ready | Explicit operator/cluster binding and application-key administration provisioned |
| Gateway ready | Correct artifact/profile deployed; authenticated read-only readiness and denial checks pass |
| Worker connected | Enrolled installation advertises a compatible model and independent heartbeat |
| Inference verified | Operator-triggered bounded request/stream succeeds through selected gateway |

Persist stage state and provider resource IDs outside a single invocation. Resume by inspecting actual state, not merely replaying every creation call. Retry transient failures with backoff and respect provider rate limits; the Management API documents a general 120 requests/minute limit per user per project/organization, with endpoint-specific exceptions. [Management API](https://supabase.com/docs/reference/api/introduction)

Serialize concurrent installs/upgrades per project. Run transactional migrations with a transaction-scoped lock and commit the migration record atomically; do not depend on a session lock surviving separate HTTP calls. Nontransactional steps need explicit reconciliation. Keep CLI migration history and installer hashes consistent; an API-applied migration must not be applied again by `db push`. Reject unexpected schema drift or a conflicting release instead of “repairing” history blindly. Merge exposed schema configuration without removing the project's other schemas.

Inject failures after every stage: browser closes, token expires, project creation response is lost, SQL commits but response is lost, secrets save but deploy fails. A retry must not create a second project, rotate a working application key unexpectedly, duplicate Cron jobs or grant an extra owner. If creation outcome is ambiguous, reconcile through recorded identifiers and operator-visible state before trying again.

Encrypt temporary management tokens, redact logs, and expire them after the setup/resume window by default. Subsequent upgrades reauthorize; retained access is an explicit operator choice. The target gateway receives only its runtime project credential, and workers only their enrolled sessions. Do not send project admin credentials to a browser onboarding script or worker. Account for the blast radius of a project-secret compromise when recommending a dedicated project.

Uninstall first disables admission, drains/cancels jobs, revokes Oshepherd credentials and removes its gateway/schedules. Preserve data by default. Destructive purge is a distinct operation listing exactly which resources it owns; never delete an existing Supabase project or uninstall the account-level Marketplace integration implicitly. Gateway rollback must respect schema compatibility and retained results.

## CLI fallback and worker onboarding

Package a guided CLI installer and doctor command alongside the web flow. The fallback uses the official Supabase CLI's login/link, migration and function deployment commands. `functions deploy --use-api` bundles remotely without Docker; local Supabase development still has its own requirements. This fallback is a first-class supported path, not a page of unexplained SQL. [Supabase CLI](https://supabase.com/docs/reference/cli)

Proposed experience, after implementation:

```sh
# Interactive setup: asks for the target project and uses protected credential storage.
oshepherd supabase install --gateway edge
oshepherd supabase doctor

# Same bootstrap protocol; emits configuration for the Vercel template.
oshepherd supabase install --gateway fastapi --hosting vercel

# On the machine that actually runs Ollama; interactive enrollment, no admin token.
oshepherd worker-login
oshepherd start-worker --backend supabase --env-file .worker.supabase.env
```

The installer wraps or explains each administrative action, pins a tested CLI version and prints a redacted summary of changes. Do not ship an arbitrary `curl | sh` path. Package installation instructions should select the published Supabase extra and an explicit release, respecting the repository's Python requirement. Validate a clean machine with no Redis and no prior local Supabase stack.

Worker enrollment uses a short-lived, single-use operator-authorized challenge bound to the cluster and worker capacity. Prompt for any secret code securely; avoid command arguments, URLs, logs and shell history. Exchange it for the installation's own Auth session and store rotated refresh tokens atomically with restrictive permissions. Do not assume SMTP or public signup is configured: provision operator-approved worker identities through the administrative enrollment flow and test the exact Auth exchange. Browserless machines need the same flow via an operator on another device.

The worker doctor checks Python/dependencies, enrollment/protocol, local Ollama reachability, model availability, host configuration and outbound Supabase access. Report “no model installed,” “worker offline,” “schema mismatch” and “gateway unavailable” separately. A cloud health check does not prove GPU inference. Model download and the first real inference are explicit user steps, with restart/service-manager guidance for continued operation.

## Operations that installation must explain

- **Cost and capacity:** select region near the database, set per-key/cluster admission limits and show quota monitoring. Vercel bills provisioned memory time as well as active CPU; waiting for a worker is not necessarily free. Auto-scaling gateways cannot create GPU capacity. [Vercel limits and usage](https://vercel.com/docs/functions/limitations)
- **Availability:** Supabase Free projects may pause after low activity over seven days. Detect paused/unavailable projects and provide restoration instructions; do not hide this with artificial keepalive traffic. Choose backup/recovery policy before production. [Supabase production guidance](https://supabase.com/docs/guides/deployment/going-into-prod)
- **Data handling:** prompts/results pass through hosted Postgres/Storage. Show region and retention choices, including backup/log retention, before cloud installation; worker operators also see prompts.
- **Upgrade ownership:** ship additive schema changes first, support overlapping worker/gateway protocols and drain old workers. Vercel redeploy/preview builds never run production migrations. A cloned template does not automatically inherit upstream security fixes; provide a version check and an explicit upgrade command.
- **Supportability:** export a credential-free diagnostic bundle with release/profile/schema IDs, readiness and recent categorized errors. Avoid prompts or output in diagnostics by default. Installer failure must leave a clear next action and the IDs of any resources already created.

## Delivery and acceptance

Implement the bootstrap manifest/CLI first, then the Supabase OAuth UI and Vercel template over that engine. The installer service and registered provider integration are explicit release dependencies with an operator/maintenance owner. They are additional work beyond the inference backend; publish CLI setup earlier if needed, but do not claim the button-assisted release is complete until the hosted flows pass.

Required artifacts: CLI commands, release manifest, migrations/config/functions, secure installer service, Vercel template and generated button, example configuration, worker enrollment, doctor, upgrade/uninstall guidance and CI smoke/fault tests. Keep Supabase-specific implementation grouped as described in the [implementation layout](implementation.md#proposed-code-layout).

Acceptance gates V16–V21 in the [matrix](compatibility.md) cover fresh installs, repeated/partial setup, ownership, previews, Vercel limits, upgrades and real onboarding. Measure time to cloud readiness and first successful inference separately. Until those tests exist and run on hosted projects, installation feasibility remains researched rather than demonstrated.
