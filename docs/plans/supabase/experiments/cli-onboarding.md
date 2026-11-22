# CLI adoption: first implementation and acceptance

> Dated evidence. Results and feature gaps below describe the recorded revision.
> See the [progress ledger](../progress.md) for current status and the
> [documentation index](../../../README.md) for current user instructions.

October 14, 2026. The user prioritized terminal-first adoption and explicitly
authorized implementation subagents. The orchestrator committed that roadmap in
`3ae5a70`, then integrated and reviewed independent join, share and provider-setup
work. The [user guide](../../../setup-supabase.md) describes the delivered surface;
the [A1–A5 plan](../cli-onboarding.md) defines the complete promise.

## Implemented scope

- `share` discovers Ollama, selects an installed model, resumes the shared operator
  engine, verifies gateway/worker/cleanup readiness, runs real bounded inference,
  and supervises worker plus Storage cleanup in the foreground. `--check` stops
  both services after verification. Existing startup functions retain their
  default standalone lifecycle and accept externally owned stop/readiness events.
- Without a prepared connection, share guides selection of an existing Supabase
  Edge project using a hidden PAT and database URL. It discovers keys and schema
  settings, generates protected configuration and binds a journaled managed Auth
  owner. It requires `psql`, explicit target consent and user Free-plan attestation;
  it does not claim independent billing verification. A completed, receipt-bound
  installation restarts without the Management token or a new provider login.
- `invite` issues an operator-authorized worker challenge and exports a portable
  private invitation. `join` securely accepts that invitation, discovers Ollama,
  persists a restricted worker session and runs in the foreground. A restart
  checks its saved project and Auth subject and reuses the session. Join reports
  worker registration, not a claim of gateway inference verification.
- Locks prevent concurrent foreground use of the same state. Ambiguous one-use
  Auth exchanges are journaled and require explicit renewal rather than silent
  replay. Invitations contain no administrative credentials; provider state stays
  on the operator host. Applied migrations, gateway contracts and qualification
  flags are unchanged.

## Review findings addressed

Cross-review added Auth-subject checks to fresh share login and resumed join,
bounded join readiness, and preserved normal Redis CLI loading through lazy
onboarding command registration. It also removed overlapping signal handlers
when worker and maintenance run together.

The final orchestrator review found that an invitation overwrite regression used
an invalid fixture publishable key and failed before exercising its intended
write. The fixture and assertion were strengthened, and export now publishes a
fully written private temporary file through an exclusive link: an output created
during enrollment is preserved. This is tested with valid invitations and a
successful export control, not merely a nonzero command exit.

## Verification

Offline coverage exercises invalid/admin/wrong-project invitations, redaction,
session substitution, consumed/uncertain challenges, interrupted install replay,
provider ownership and lost acknowledgements, worker/maintenance failure,
inference failure, credential-independent resume and invitation output races.
Provider wizard HTTP/Auth tests are mocked; they are not fresh hosted acceptance.

```sh
UV_CACHE_DIR=/private/tmp/oshepherd-uv-cache uv run --locked --no-sync pytest -q \
  tests/contracts tests/test_systemone.py
UV_CACHE_DIR=/private/tmp/oshepherd-uv-cache uv run --locked --no-sync pytest -q \
  tests/integration/supabase/test_operator.py \
  tests/integration/supabase/test_maintenance.py \
  tests/integration/supabase/test_worker.py -k 'not local_ollama_model' --tb=short
```

The focused real local integration selection passed **24 tests in 22.78 seconds**.
The final offline suite passed **454 tests in 14.44 seconds**, with the existing
upstream Starlette TestClient deprecation warning. The final installed-wheel report
`.oshepherd/local/onboarding-reviewed-20261014.json` records all checks passing,
including foreground SIGINT; it uses the reviewed candidate from
`.oshepherd/local/onboarding-reviewed-build/`. The [ledger](../progress.md)
records the exact scope and retained private evidence.
The unchanged strict Edge disconnect regression was not rerun for this Python
onboarding slice; its previous failure remains open, as does the hosted Edge failure.

The standalone `scripts/supabase_onboarding_check.py` runs from outside the checkout
under an isolated uv environment using the built wheel. It prepares a new cluster
on the existing isolated local Supabase stack and a loopback persistent gateway,
then exercises the installed share/invite/join CLI with the existing
`llama3.2:3b` model. It verifies repeated share identity and SQL generation advance,
foreground SIGINT shutdown, separate joined-worker state, no administrative secrets
in joined files, join restart, genuine JSON and terminal NDJSON. This simulates a
second machine through a separate state/process on the same host; it does not claim
testing a second physical machine or a fresh hosted project.

Reproduce with an absolute script/wheel path from a clean directory:

```sh
uv run --isolated --no-project --python 3.12 \
  --with '/absolute/candidate.whl[supabase]' \
  --with-requirements /absolute/repo/requirements/runtime.txt \
  python /absolute/repo/scripts/supabase_onboarding_check.py \
  --local-from /absolute/repo/.oshepherd/local/connection.json \
  --work-dir /absolute/private/fresh-check \
  --report-file /absolute/private/check-report.json --model llama3.2:3b
```

The harness stops its processes and disables its owned cluster, retaining private
evidence/journals. It does not stop the user's Ollama/local stack, download models,
mutate cloud resources or reuse retired qualification gateways. It tests the
existing-installation path; initial local setup in the harness is not represented
as automatic cloud provisioning.

## October 31 local-first acceptance

The [ledger](../progress.md#local-first-validation-of-candidate-r3) holds results
and limits. All of it used local containers and the r3 wheel from
`.oshepherd/local/candidate-20261031-r3/`.

**macOS services.** Add `--service-check` to the command above. Report:
`.oshepherd/local/adoption-service-20261031-r3-report.json`, `passed: true`,
`service_platform: Darwin`.

**Linux services.** A disposable `debian:12-slim` image with `systemd`, `systemd-sysv`,
`dbus-user-session`, `libpam-systemd`, `socat`, `postgresql-client`, uv 0.9.18 and a
`worker` user with `/var/lib/systemd/linger/worker`. Run it with `--privileged
--cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock`.
Inside it, `socat` forwards loopback 55321, 55322 and 11434 to
`host.docker.internal`, so the harness's isolated-origin checks still apply. Run
the same harness as `worker` with `XDG_RUNTIME_DIR=/run/user/1000`. Reports:
`.oshepherd/local/adoption-linux-systemd-20261031-r3-report.json` (`passed: true`,
`service_platform: Linux`) and `-faults.json`, which records the injected
`daemon-reload` failure and interrupted-install recovery. Do not run Edge tests
while this container is up; see the ledger.

**Management SQL.** Start a second Supabase stack from a copy of `supabase/config.toml`
with its own `project_id` and ports and no Edge functions. Run `postgres-meta` on
that stack's Docker network, bound to loopback and connected as `postgres`, then:

```sh
uv run --locked python -m scripts.supabase_management_local_check \
  --status-file /private/stack-status.json --meta-url http://127.0.0.1:56380
```

`stack-status.json` is `supabase status -o json` output. The first run proves the
privilege gate rejects before mutation. After `grant usage on schema storage to
postgres with grant option` as `supabase_admin`, a rerun migrates the fresh schema.
Stop the stack with `supabase stop --no-backup` for that workdir. The check refuses
the 55321 development stack.

## Remaining product and release work

Subsequent source changes implement new Supabase project creation with durable
reconciliation, Free-plan checks, Management SQL without database URL/`psql`
input, and optional service management. Full fresh hosted wizard acceptance
remains open; publication is deferred. See the current [ledger](../progress.md). Provider admin credentials
remain in private operator files; renewed setup can reauthorize a saved expired PAT.
All compatible models remain advertised rather than being restricted by the
selected inference-check model. Storage cleanup needs the operator process running
even if joined workers remain online.

Hosted OAuth/buttons are a later distribution path. Neither improved CLI onboarding
nor local inference closes the known hosted cancellation gate or broader resource
qualification requirements. Continue with [next steps](../next-steps.md).
