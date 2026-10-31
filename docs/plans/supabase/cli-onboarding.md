# Adoption-first CLI onboarding

Updated October 31, 2026. This is the current implementation priority, ahead of
hosted OAuth/buttons and additional benchmark work. Existing inference contracts,
ownership checks and experimental status remain in force. This document describes
the target experience; the source candidate now includes `share`, `invite`, `join`, provider creation/selection
and optional services. The full A1–A5 acceptance and publication promise remains incomplete.

## Product promise

A user with uv, a running Ollama server and an installed model enters one command
to begin sharing. Guided account consent and explicit resource selection are part
of that command. No repository checkout, handwritten provider JSON, copied Auth
UUID, external installer service or manually assembled environment file should be
required in the finished flow. Ollama/model installation remains a separate choice.

The proposed published entry points are:

```sh
uvx --from 'oshepherd[supabase]' oshepherd share
uvx --from 'oshepherd[supabase]' oshepherd join
```

These are target distribution commands, not instructions to run against the currently published
package. Use the [candidate sharing guide](../../share-ollama.md) for implemented
commands and remaining prerequisites. Publish a tested version before advertising
the uvx commands as available. uv manages
Python/dependencies; resolve or clearly report any remaining external tooling.

`share` provisions or connects an installation, enrolls this machine, starts its
worker and verifies a bounded real inference request. `join` attaches another
machine to an existing Oshepherd installation using a securely entered one-time
invitation or private invitation file, without provider administrator credentials.
Existing provider projects still need bootstrap; an existing Oshepherd installation
only needs worker enrollment. A worker makes outbound connections: do not expose
Ollama's local port or configure inbound tunnels.

Supabase is required for this backend. Edge is its default gateway; Vercel is an
optional gateway using the same Supabase installation. There is no Vercel-only
backend in this scope. Existing Redis commands retain their defaults.

## Journey and readiness

1. Discover local Ollama and installed models; offer explicit model/server selection.
   Fail usefully when Ollama is unavailable or no model is installed; never download
   a model implicitly.
2. Choose create, use existing provider projects, or join an existing installation.
   Authenticate providers locally with hidden credentials or a verified supported
   login flow. A hosted OAuth application is not a CLI milestone prerequisite.
3. Show the organization/team, project, region, profile and proposed changes. Require
   deliberate selection before writes; honor Free/Hobby-only operation and never
   silently upgrade billing. Preserve unrelated resources and reject collisions.
4. Provision project services, migrations, private Storage, cleanup, owner and gateway
   through the shared versioned engine. Persist operation identity before writes;
   reconcile uncertain outcomes instead of duplicating resources on retries.
5. Enroll and start the worker. Keep project/admin credentials in operator state;
   portable worker invitations contain only the minimum enrollment configuration.
6. Report cloud ready, worker connected and inference verified separately. Return
   the authenticated endpoint and a private client configuration path. No success
   message based solely on deployment readiness or an unverified worker task.
7. Explain foreground lifetime and restart. Offer optional service installation only
   when implemented and selected; integrate ongoing Storage cleanup on the operator
   host without transferring its secret to joined machines.

## Ordered implementation slices

| Slice | Deliverable | Acceptance |
| --- | --- | --- |
| A1: join and local discovery | Portable versioned invitation, hidden-code/private-file input, restricted session, Ollama discovery, resumable foreground worker | Second machine has no admin keys; rejects wrong-project/invalid invitations; restart reuses session; errors redact credentials |
| A2: share orchestration | One resumable CLI entry over current install/deploy/enroll/verify engine; initially accepts existing protected operator configuration | Existing configured target reaches actual inference; partial failure resumes with stable identity; no claim of full provisioning yet |
| A3: provider setup | Guided auth/discovery, create/select Supabase and optional Vercel projects, owner bootstrap and connection discovery | Fresh account/project and existing-project paths need no handwritten files/UUIDs; bounded readiness; ambiguous creation reconciled; free-plan checks and collisions enforced |
| A4: ongoing operation | Integrated worker/maintenance supervision, restart/status, optional service management | Foreground shutdown is clean; cleanup stays active while sharing; reconnect/revocation work; joined workers receive no admin secret |
| A5: distribution and user acceptance | Published pinned package, concise user guide, fresh-machine tests | One uv entry from outside checkout to genuine JSON/NDJSON; repeat on second machine and existing projects; interruptions/upgrades/removal tested |

The October 31 implementation covers A1/A2, A3 provider creation/selection and
Management SQL, A4 foreground supervision/user-service management, and A5 candidate
verification plus an explicitly gated publication workflow. These implementation
claims do not establish fresh hosted, real service-manager or published-package
acceptance. Do not mark A3 complete merely because mocked creation passes.
The orchestrator coordinates subagents, reviews their integration and maintains
one evidence ledger. Work in separate files where possible; no agent edits applied
migrations or deploys to an unspecified provider target.

## Security and acceptance boundaries

Invitations are bearer secrets: short-lived, one-use, project-bound, privately
stored, never command arguments, URLs or routine output. Worker state uses existing
private-file/session protection and refresh behavior. Local operator locks and
transactional migration locks remain authoritative. Cloud/project creation needs
durable intent and reconciliation; a lost response is not permission to create
again. Do not weaken resource ownership/adoption checks to simplify onboarding.

Current hosted qualification resources are retired. Local deterministic and
existing isolated local-stack validation may proceed. New live cloud writes need
explicitly selected disposable targets; preserved project-wide secrets must not be
overwritten for a new cluster blindly. Existing authorization does not create a
new target or justify paid upgrades.

The known Edge/Vercel disconnect failure remains a release gate. CLI usability can
be delivered and tested as experimental independently; it cannot establish hosting
qualification. Hosted OAuth/buttons are a later optional distribution path.
Broader resource/cost measurements and strict cancellation checks remain in
[next steps](next-steps.md), with the unchanged [compatibility matrix](compatibility.md).

## Starting gap and evidence

At adoption-priority approval, the candidate wheel supported separate operator
configure/install/deploy/enroll/login/start commands. Provider adapters and local
installed-wheel lifecycle are tested. Automatic project creation, the combined
share/join journey, automated service setup and a published candidate were missing.
The October 31 candidate extends this with create-or-select Supabase organizations
and projects, documented optional plan verification with explicit fallback
attestation, bounded readiness and durable uncertain-creation reconciliation.
Management SQL reuses the transactional migration engine without a database URL
or external `psql`. Guided optional Vercel uses Hobby selection, provider REST
deployment and the verified installed runtime rather than a provider CLI or
manually supplied wheel. Dedicated user services support foreground-verified
share and joined-worker state on macOS/Linux.

The [distribution workflow](experiments/distribution.md) builds and verifies
`0.0.25a1` artifacts and gates explicit experimental publication on prerelease
versions, exact hashes and the protected PyPI environment. The candidate is not
published. Service-manager operation has local evidence on macOS and containerized
Linux systemd. Fresh hosted creation and clean-machine published-package acceptance
still require their own evidence; validation is recorded in the ledger. Hosted
cancellation remains unqualified.

The [progress ledger](progress.md) records each implemented slice and distinguishes
mocked adapter tests, genuine local inference and fresh hosted acceptance.
