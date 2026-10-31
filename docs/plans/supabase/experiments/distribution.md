# Candidate distribution and explicit experimental publication

Updated October 31, 2026. This is packaging infrastructure, not evidence that a
published package or hosted profile is qualified. Both profiles remain
`qualified: false`; strict disconnect cancellation is still an open gate.

## Local artifact verification

From the checkout, after the release manifest and dependency exports have been
refreshed and reviewed:

```sh
uv sync --locked --extra supabase --extra installer
uv run --locked python -m scripts.verify_candidate \
  --build --dist .oshepherd/local/distribution-candidate \
  --report .oshepherd/local/distribution-candidate-verification.json
```

The output directory must be absent or empty. `uv build` builds the sdist and then
the wheel from that sdist. Verification checks:

- Exactly one wheel and one sdist, matching project/package versions and console
  entry point.
- Packaged Python, SQL, Edge and Vercel assets match the current source manifest
  and source bytes; omitted or unexpected package files fail validation.
- Archives contain no tests, `.oshepherd`, environment files, worker sessions,
  known provider/connection files, unsafe paths or archive links.
- The exact wheel installs through uv in an isolated environment outside the
  checkout. The installed release hashes and `oshepherd`, `share`, `join`,
  `invite`, `service` and `supabase` help entry points work without provider
  credentials, Docker, Redis, Ollama or a database server.
- A JSON report records package version, artifact SHA256 values, manifest SHA256,
  source Git commit and the unchanged profile qualification flags.

The CLI smoke checks are not real inference acceptance. The separate
[onboarding acceptance](cli-onboarding.md) and
[independent acceptance](independent-acceptance.md) reports describe lifecycle
coverage. Packaging checks do not waive any inference, cancellation or hosted
validation requirement.

To verify previously built artifacts against their earlier report:

```sh
uv run --locked python -m scripts.verify_candidate \
  --dist .oshepherd/local/distribution-candidate \
  --expected-report .oshepherd/local/distribution-candidate-verification.json
```

A changed source commit, source manifest or distribution hash fails the comparison.
Local candidate verification also permits stable/local version labels, but the
publication gate requires a non-local prerelease or development version. The
current unpublished candidate is `0.0.25a1`; the published `0.0.24` does not contain
this onboarding workflow. Add `--require-prerelease` to enforce the experimental
publication gate without changing any files.

## Manual workflow

[experimental candidate package](../../../../.github/workflows/candidate-release.yaml)
is triggered only by `workflow_dispatch`. Its `publish` input defaults to false.
The default run executes offline contracts, builds and verifies artifacts, then
uploads the exact wheel, sdist and verification report as one workflow artifact.
It does not push commits, change versions or publish packages.

With `publish: true`, the build job additionally requires a PEP 440 prerelease or
development version, such as a separately reviewed `0.0.25a1`. Then:

1. A `reverify` job with no publishing permission checks out the same source
   commit, downloads that run's verified artifact and revalidates exact artifact
   hashes, source/manifest identity, prerelease version and installed CLI commands.
2. The `publish` job waits for the repository's `pypi` environment protection rules.
3. It downloads the same artifact and checks its hashes against the report. It
   does not check out or run project or dependency code, because any such step
   could request the job's OIDC token.
4. It publishes only those two verified distribution files through
   `uv publish --trusted-publishing always`.

There is no automatic version bump or static PyPI token. The workflow does not
change release qualification flags or suppress strict runtime tests. Publishing an
experimental prerelease is a separate, explicit release decision.

## External prerequisites and remaining acceptance

The workflow file alone cannot configure GitHub environment protection or PyPI
ownership. Before requesting publication:

- Review an unused prerelease version in `pyproject.toml` and `setup.py`, update the
  lock/export files as needed, and regenerate the release manifest after final
  source changes. Do not reuse an already published version.
- Configure the GitHub `pypi` environment with required reviewers and an explicit
  allowed branch/tag policy. Without those settings, naming an environment does
  not itself create an approval gate.
- Configure the PyPI project Trusted Publisher for this repository, workflow
  filename `candidate-release.yaml`, and environment `pypi`.
- The legacy `bump-and-release.yaml` version-rewriting workflow is now manual-only.
  Do not run it over a reviewed candidate version: its GitHub release `prerelease`
  label does not itself guarantee a PEP 440 prerelease package version. Ordinary
  main pushes no longer trigger that rewrite. The new publish gate rejects stable
  versions.
- Run the default verification workflow on the exact reviewed candidate commit,
  inspect its evidence, and separately authorize a publication run.
- After publication, validate the pinned `uvx --from 'oshepherd[supabase]==VERSION'
  oshepherd share` and `join` journeys from clean machines, including the selected
  cloud targets and one-time invitations. Never claim this acceptance based on
  `--help` alone.

No remote publication, environment setup or trusted-publisher registration was
performed while implementing this infrastructure. The local package version was
advanced to the explicitly reviewed experimental candidate `0.0.25a1`. Distribution remains
pending those external release steps and actual published-artifact acceptance.

## October 31 verification evidence

The actual `0.0.25a1` sdist and wheel passed archive/source/manifest verification
and installed CLI help checks in an isolated uv environment outside the checkout.
The private report is `.oshepherd/local/candidate-verification-20261031.json`.
The initial build run correctly exposed a verifier incompatibility with uv's
one-byte `dist/.gitignore` marker; the verifier now permits only that regular-file
marker and a regression rejects other hidden content. Seventeen artifact contract
tests pass. The final repository suite and live lifecycle results belong in the
[progress ledger](../progress.md); package help checks do not establish inference.

The pinned CI uv 0.9.18 successfully read lockfile revision 5 and generated matching
full/runtime exports with `--locked --offline`; the existing CI pin is retained.

The current candidate is r3, which supersedes earlier evidence after service fixes:
wheel SHA256 `f62659a957c4bc987867f630e780ed8b5f69770929df27e246fc273bd9ef45dc`,
sdist SHA256 `0bdb12c14cf60c678bb875d1983224b6c6190fce5aad9623aacaa66d4939576d`,
manifest SHA256 `5f61f66a0c7057f6cbdd1ef4c0729f7f22fb9ea22616c5ca375d0ec7a3161339`.
Both profiles stay `qualified: false`. The artifacts are in
`.oshepherd/local/candidate-20261031-r3/`.

The separated `reverify`/`publish` jobs parse as valid workflow YAML, but they have
never run on GitHub. A first run is part of the external prerequisites above.

## Sources

The build and publish behavior follows the official
[uv package guide](https://docs.astral.sh/uv/guides/package/) and
[uv GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/).
Trusted Publisher registration and environment binding are documented by
[PyPI](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
The workflow pins official action commits verified from the repositories for
[checkout v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2),
[upload-artifact v4.6.2](https://github.com/actions/upload-artifact/releases/tag/v4.6.2)
and [download-artifact v4.3.0](https://github.com/actions/download-artifact/releases/tag/v4.3.0).
