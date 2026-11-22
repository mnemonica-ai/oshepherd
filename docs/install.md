# Install oshepherd

For the Supabase workflow, install the
[0.1.0rc1 release candidate](#install-the-010rc1-candidate), then choose the
[local Docker walkthrough](supabase-walkthrough.md) or the
[Supabase Cloud walkthrough](supabase-cloud-walkthrough.md).

| Setup | Guide | Package |
| --- | --- | --- |
| Supabase, local Docker | [Local walkthrough](supabase-walkthrough.md) | `oshepherd[supabase]==0.1.0rc1` or this checkout |
| Supabase Cloud | [Cloud walkthrough](supabase-cloud-walkthrough.md) | `oshepherd[supabase]==0.1.0rc1` or this checkout |
| Redis, legacy self-hosted setup | [Redis](setup-redis.md) | `oshepherd` (latest stable) or this checkout |

The Supabase backend is published as an experimental release candidate. Plain
`oshepherd` installs, including the unpinned commands under
[the legacy published package](#install-the-legacy-published-package-with-uv),
still get the latest stable version, which does not include Supabase.

## Install the 0.1.0rc1 candidate

Pin the version: uv and pip skip release candidates unless you ask for one. Keep
the `[supabase]` suffix; it adds the libraries the Supabase setups need:

```sh
uv tool install --python 3.12 'oshepherd[supabase]==0.1.0rc1'
oshepherd share --help
```

If an older oshepherd is already installed, add `--force` to replace it. To run it
once without installing, use `uvx --from 'oshepherd[supabase]==0.1.0rc1' oshepherd share`.
Background services need an installed package, not `uvx`.

### From a checkout

To test unreleased changes, run from a checkout instead:

```sh
uv sync --locked --extra supabase
uv run --locked oshepherd --help
```

Services refuse editable installs. For a global command or background services
from a checkout, build and verify a wheel; the output directory must be new or
empty:

```sh
uv run --locked python -m scripts.verify_candidate --build \
  --dist .oshepherd/local/candidate --report .oshepherd/local/candidate.json
uv tool install --python 3.12 '/absolute/path/oshepherd-0.1.0rc1-py3-none-any.whl[supabase]'
```

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

## Install with pip

uv is recommended, but pip works in a virtual environment you manage:

```sh
python3.12 -m venv ~/.venvs/oshepherd
~/.venvs/oshepherd/bin/pip install oshepherd
~/.venvs/oshepherd/bin/oshepherd --help
```

Those commands install the latest stable package. For Supabase, install the
candidate instead:

```sh
~/.venvs/oshepherd/bin/pip install 'oshepherd[supabase]==0.1.0rc1'
```

Background services accept this environment as long as it stays in place. Don't
create it under a temporary or cache directory. When replacing an installed
candidate, uninstall its service first and reinstall the service afterwards as
described under [upgrade or remove](#upgrade-or-remove).

## Ask a coding agent to install it

Paste this into a coding agent such as Claude Code. Replace `PACKAGE` with
`oshepherd` for the Redis setup, or with `'oshepherd[supabase]==0.1.0rc1'` for
Supabase setups:

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
