# Install oshepherd

For the primary Supabase workflow, test this branch from a checkout:

```sh
uv sync --locked --extra supabase
uv run --locked oshepherd --help
```

Choose the [local Docker walkthrough](supabase-walkthrough.md) or the
[Supabase Cloud walkthrough](supabase-cloud-walkthrough.md). No publication is
needed. For a global command or background services, build and verify the local
wheel using [the candidate instructions](#install-the-0025a1-candidate).

| Setup | Guide | Package |
| --- | --- | --- |
| Supabase, local Docker | [Local walkthrough](supabase-walkthrough.md) | This checkout with `[supabase]` |
| Supabase Cloud | [Cloud walkthrough](supabase-cloud-walkthrough.md) | This checkout or its verified `0.0.25a1` wheel with `[supabase]` |
| Redis, legacy self-hosted setup | [Redis](setup-redis.md) | Existing published package or this checkout |

The Supabase candidate is not published to PyPI. The unpinned package commands
below describe the legacy published installation; do not use them to obtain this
branch's Supabase changes. Public publication is outside the current milestone.

## Install the legacy published package with uv

1. Install uv, if you don't have it:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Install oshepherd as a global command. uv downloads Python 3.12 if needed and
   keeps oshepherd in its own environment, isolated from other Python projects:

   ```sh
   uv tool install --python 3.12 oshepherd
   ```

3. Check that the command works:

   ```sh
   oshepherd --help
   ```

   If your shell reports `oshepherd: command not found`, run `uv tool update-shell`
   and open a new terminal. uv puts tool commands in `~/.local/bin`.

### One command

The same two steps as one line, for a new machine:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh && ~/.local/bin/uv tool install --python 3.12 oshepherd
```

The full `~/.local/bin/uv` path is there because a freshly installed uv is not on
your `PATH` until you open a new terminal. This pipes a remote script into your
shell. If you prefer to read it first, download
`https://astral.sh/uv/install.sh` and run it yourself.

### Upgrade or remove

```sh
uv tool upgrade oshepherd
uv tool uninstall oshepherd
```

If you installed [background services](setup-supabase.md#run-in-the-background),
run `oshepherd service uninstall` **before** you uninstall or upgrade oshepherd.
Otherwise the service keeps trying to start a program that has gone or changed.
After an upgrade, install the service again. Uninstalling oshepherd leaves your
saved state and cloud resources in place.

## Install the 0.0.25a1 candidate

Use this to try the Supabase setups before they reach PyPI. You need the candidate
wheel file, `oshepherd-0.0.25a1-py3-none-any.whl`.

To build and verify it from a checkout of the repository that contains the
candidate, run this in the checkout. The output directory must be new or empty:

```sh
uv run --locked python -m scripts.verify_candidate --build \
  --dist .oshepherd/local/candidate --report .oshepherd/local/candidate.json
```

Install that wheel as a global command, using its absolute path. Keep the
`[supabase]` suffix; it adds the libraries the Supabase setups need:

```sh
uv tool install --python 3.12 '/absolute/path/oshepherd-0.0.25a1-py3-none-any.whl[supabase]'
oshepherd share --help
```

If an older oshepherd is already installed, add `--force` to replace it.

For background services, install the wheel: services require the exact installed
package and refuse editable installs. Foreground local testing can use
`uv sync --locked --extra supabase` from this checkout; start with the
[Supabase walkthrough](supabase-walkthrough.md).

Publishing to PyPI is outside the current milestone. Use the checkout or a
locally built and verified candidate wheel for Supabase testing.

## Install with pip

uv is recommended, but pip works in a virtual environment you manage:

```sh
python3.12 -m venv ~/.venvs/oshepherd
~/.venvs/oshepherd/bin/pip install oshepherd
~/.venvs/oshepherd/bin/oshepherd --help
```

For Supabase setups, install `'oshepherd[supabase]'` instead, or the candidate
wheel path with `[supabase]` until it is published. Background services accept
this environment as long as it stays in place. Don't create it under a temporary
or cache directory.

## Ask a coding agent to install it

Paste this into a coding agent such as Claude Code. Replace `PACKAGE` with
`oshepherd` for the Redis setup, or with the quoted candidate wheel path ending in
`[supabase]` for Supabase setups:

```text
Install the oshepherd command-line tool on this machine as a global command.
1. If `uv --version` fails, install uv with its official installer:
   curl -LsSf https://astral.sh/uv/install.sh | sh
2. Run: uv tool install --python 3.12 PACKAGE
3. Make sure `oshepherd --help` works from a new terminal. If it is not found,
   run `uv tool update-shell`.
4. Report the output of `uv tool list` and `oshepherd --help`.
Stop there. Do not run `oshepherd share`, `join` or `start-*`, and do not create,
ask for or store any tokens, passwords or env files. I will do the setup myself.
```

The last instruction matters: setup asks for provider tokens, and those should go
into oshepherd's hidden prompts, not into an agent conversation.
