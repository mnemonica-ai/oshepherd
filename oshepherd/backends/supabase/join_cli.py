"""User-facing, resumable foreground worker enrollment."""

import asyncio
import hashlib
import signal
from pathlib import Path

import click
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .client import RpcError
from .config import SupabaseWorkerConfig
from .onboarding import (
    OnboardingError,
    WorkerInvitation,
    default_state_dir,
    discover_ollama,
)
from .operator import installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError
from .sessions import WorkerSession
from .startup import login_worker, start_worker


class JoinState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: int = 1
    supabase_url: str
    publishable_key: SecretStr
    schema_name: str = Field(alias="schema")
    capacity: int
    ollama_url: str
    model: str
    attempted_token_digest: str | None = None
    accepted_invitation_digest: str | None = None
    session_user_id: str | None = None

    def save(self, path):
        data = self.model_dump(mode="json", by_alias=True)
        data["publishable_key"] = self.publishable_key.get_secret_value()
        write_private_json(path, data)

    def worker_config(self, directory):
        if self.v != 1:
            raise OnboardingError("Unsupported saved join configuration")
        return SupabaseWorkerConfig(
            SUPABASE_URL=self.supabase_url,
            SUPABASE_PUBLISHABLE_KEY=self.publishable_key,
            SUPABASE_SCHEMA=self.schema_name,
            CONCURRENCY=self.capacity,
            OLLAMA_BASE_URL=self.ollama_url,
            WORKER_SESSION_FILE=directory / "worker-session.json",
        )


def _same_installation(state, invitation):
    return (
        state.supabase_url == invitation.supabase_url
        and state.publishable_key == invitation.publishable_key
        and state.schema_name == invitation.schema_name
        and state.capacity == invitation.capacity
    )


async def prepare_join(
    directory, *, invitation=None, ollama_url=None, model=None, select_model=None
):
    """Validate discovery and durable identity before any one-time Auth exchange."""
    directory = Path(directory)
    state_file = directory / "join.json"
    state = (
        JoinState.model_validate(read_private_json(state_file))
        if state_file.exists()
        else None
    )
    if state is not None:
        state.worker_config(
            directory
        )  # Validate persisted configuration before network IO.
        if invitation and not _same_installation(state, invitation):
            raise OnboardingError(
                "Invitation differs from saved installation; use a separate --state-dir"
            )
        if ollama_url and ollama_url.rstrip("/") != state.ollama_url:
            raise OnboardingError(
                "Ollama URL differs from saved configuration; use a separate --state-dir"
            )
        if model and model != state.model:
            raise OnboardingError(
                "Model differs from saved configuration; use a separate --state-dir"
            )
    elif invitation is None:
        raise OnboardingError("A worker invitation is required for the first join")
    session_file = directory / "worker-session.json"
    if session_file.exists():
        if state is None:
            raise OnboardingError(
                "Existing worker session has no matching join configuration"
            )
        session = WorkerSession.load(session_file, supabase_url=state.supabase_url)
        if state.session_user_id is not None and state.session_user_id != str(
            session.user_id
        ):
            raise OnboardingError(
                "Saved session belongs to another worker; restore the matching session or use a separate --state-dir"
            )
        if state.session_user_id is None:
            if state.attempted_token_digest is None:
                raise OnboardingError(
                    "Saved session has no recorded enrollment; use a separate --state-dir"
                )
            # Recover only the narrow crash window after login saved the session.
            state.session_user_id = str(session.user_id)
            state.accepted_invitation_digest = state.attempted_token_digest
        if (
            invitation
            and hashlib.sha256(
                invitation.token_hash.get_secret_value().encode()
            ).hexdigest()
            != state.accepted_invitation_digest
        ):
            raise OnboardingError(
                "A worker session already exists; restart without an invitation or use a separate --state-dir"
            )
    discovery = await discover_ollama(
        url=state.ollama_url if state else ollama_url or "http://127.0.0.1:11434",
        model=state.model if state else model,
    )
    selected = discovery.model
    if selected is None:
        if select_model is None:
            raise OnboardingError(
                "Multiple Ollama models are installed; select one with --model"
            )
        selected = select_model(discovery.models)
        if selected not in discovery.models:
            raise OnboardingError(
                "Selected model is not installed on this Ollama server"
            )
    if state is None:
        state = JoinState(
            supabase_url=invitation.supabase_url,
            publishable_key=invitation.publishable_key,
            schema=invitation.schema_name,
            capacity=invitation.capacity,
            ollama_url=discovery.url,
            model=selected,
        )
    config = state.worker_config(directory)
    if not session_file.exists():
        if invitation is None:
            raise OnboardingError(
                "No saved session; provide a fresh invitation to finish enrollment"
            )
        digest = hashlib.sha256(
            invitation.token_hash.get_secret_value().encode()
        ).hexdigest()
        if state.attempted_token_digest == digest:
            raise OnboardingError(
                "Previous enrollment outcome is uncertain; request a renewed invitation"
            )
        # A crash after Auth consumes a one-use challenge must not silently replay it.
        state.attempted_token_digest = digest
        state.save(state_file)
        await login_worker(config, invitation.token_hash.get_secret_value())
        session = WorkerSession.load(session_file, supabase_url=config.SUPABASE_URL)
        state.session_user_id = str(session.user_id)
        state.accepted_invitation_digest = digest
    state.attempted_token_digest = None
    state.save(state_file)
    return config


async def run_join(config, *, readiness_seconds=60):
    ready, stop = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = []
    worker = readiness = None
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
            signals.append(signum)
        worker = asyncio.create_task(start_worker(config, stop=stop, ready=ready))
        readiness = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait(
            {worker, readiness},
            timeout=readiness_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise OnboardingError(
                "Worker connection timed out; check the installation and retry using saved state"
            )
        if worker in done:
            await worker
            if not stop.is_set():
                raise OnboardingError("Worker stopped before connecting")
            return
        click.echo(
            "Worker connected. Foreground sharing is active; press Ctrl-C to stop."
        )
        click.echo(
            "Inference has not been verified by join. The worker advertises all compatible installed models."
        )
        await worker
        if not stop.is_set():
            raise OnboardingError("Worker stopped unexpectedly")
    finally:
        stop.set()
        for task in (worker, readiness):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (worker, readiness) if task is not None),
            return_exceptions=True,
        )
        for signum in signals:
            loop.remove_signal_handler(signum)


def _choose_model(models):
    # Numeric choice avoids echoing server-controlled model names into a prompt.
    click.echo("Installed Ollama models:")
    for index, name in enumerate(models, 1):
        click.echo(f"  {index}. {name}")
    choice = click.prompt("Select model", type=click.IntRange(1, len(models)))
    return models[choice - 1]


@click.command("join")
@click.option(
    "--invitation-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Private 0600 worker invitation; never pass its contents as an argument.",
)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="Private worker state directory; defaults to the user's Oshepherd state directory.",
)
@click.option(
    "--ollama-url",
    help="Running Ollama server origin; defaults to http://127.0.0.1:11434 on first join.",
)
@click.option(
    "--model",
    help="Installed model to select during discovery; does not restrict the worker's model inventory.",
)
def join(invitation_file, state_dir, ollama_url, model):
    """Join an existing installation or restart its saved foreground worker."""
    try:
        directory = state_dir or default_state_dir()
        with installation_lock(directory) as directory:
            invitation = (
                WorkerInvitation.load(invitation_file) if invitation_file else None
            )
            if invitation is None and not (directory / "worker-session.json").exists():
                invitation = WorkerInvitation.decode(
                    click.prompt("Private one-time invitation code", hide_input=True)
                )
            config = asyncio.run(
                prepare_join(
                    directory,
                    invitation=invitation,
                    ollama_url=ollama_url,
                    model=model,
                    select_model=_choose_model,
                )
            )
            click.echo(
                "Experimental Supabase worker: session saved privately. Connecting to the installation..."
            )
            asyncio.run(run_join(config))
    except OnboardingError as error:
        raise click.ClickException(str(error)) from None
    except (RpcError, OperatorError) as error:
        raise click.ClickException(str(error)) from None
    except (OSError, ValueError, TypeError, TimeoutError):
        raise click.ClickException(
            "Join failed; check the invitation, private state and running Ollama server"
        ) from None
