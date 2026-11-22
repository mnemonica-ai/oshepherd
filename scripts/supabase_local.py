"""Start/inspect only this repository's local Supabase stack without printing keys.

uv run --locked python -m scripts.supabase_local start
uv run --locked python -m scripts.supabase_local status
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".oshepherd" / "local"
EXCLUDED = "realtime,studio,logflare,vector,postgres-meta,supavisor,imgproxy,mailpit"
SCHEDULES = (
    (
        "oshepherd-spike-reap-v1",
        "10 seconds",
        "set statement_timeout = '5s'; set lock_timeout = '100ms'; "
        "select oshepherd_spike.scheduled_sweep('reap');",
    ),
    (
        "oshepherd-spike-purge-v1",
        "* * * * *",
        "set statement_timeout = '5s'; set lock_timeout = '100ms'; "
        "select oshepherd_spike.scheduled_sweep('purge'); "
        "delete from cron.job_run_details where runid in("
        "select d.runid from cron.job_run_details d join cron.job j on j.jobid = d.jobid "
        "where j.jobname in('oshepherd-spike-reap-v1','oshepherd-spike-purge-v1') "
        "and d.end_time < clock_timestamp()-interval '2 days' "
        "order by d.runid limit 256);",
    ),
)


def schedule_statements(*, remove=False):
    """Only exact owned job commands can be updated or removed."""
    statements = [
        "select pg_advisory_xact_lock(hashtextextended('oshepherd_spike_schedule_v1',0));"
    ]
    if not remove:
        statements.append("create extension if not exists pg_cron;")
    for name, interval, command in SCHEDULES:
        quoted = "'" + command.replace("'", "''") + "'"
        statements.append(
            "do $$ begin if exists(select 1 from cron.job "
            f"where jobname = '{name}' and username = current_user and command <> {quoted}) "
            "then raise exception 'oshepherd_schedule_collision'; end if; end $$;"
        )
        statements.append(
            f"select cron.unschedule(jobid) from cron.job where jobname = '{name}' "
            "and username = current_user;"
            if remove
            else f"select cron.schedule('{name}','{interval}',{quoted});"
        )
    return statements


def private_write(path, data):
    if path.is_symlink():
        raise SystemExit("Refusing a symlink for local state")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(data)


def connection_from_output(output):
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "API_URL" in value:
            return value
    try:
        value = json.loads(output)
    except ValueError:
        return None
    return value if isinstance(value, dict) and "API_URL" in value else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "start",
            "status",
            "apply-spike",
            "schedule-cleanup",
            "unschedule-cleanup",
        ),
    )
    args = parser.parse_args()
    config = tomllib.loads((ROOT / "supabase" / "config.toml").read_text())
    if (
        config["project_id"] != "oshepherd-development"
        or config["api"]["port"] != 55321
    ):
        raise SystemExit(
            "Refusing a stack other than the isolated oshepherd-development profile"
        )
    toolchain = json.loads((ROOT / "supabase" / "toolchain.json").read_text())
    env = dict(os.environ)
    env.pop("SUPABASE_ACCESS_TOKEN", None)
    version = subprocess.run(
        ["supabase", "--version"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if version != toolchain["supabase_cli"]:
        raise SystemExit(
            "Supabase CLI version differs from the checked-in test toolchain"
        )
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or STATE.parent.is_symlink():
        raise SystemExit("Local credential directories cannot be symlinks")
    STATE.chmod(0o700)
    STATE.parent.chmod(0o700)
    if args.action in ("apply-spike", "schedule-cleanup", "unschedule-cleanup"):
        # Static local-development credentials/ports from this profile. No
        # arbitrary SQL path, remote target or existing account is accepted.
        env.update(PGPASSWORD="postgres")
        statements = ["begin;"]
        for filename in (
            ()
            if args.action != "apply-spike"
            else (
                "identity.sql",
                "queue.sql",
                "storage.sql",
                "maintenance.sql",
                "cron.sql",
                "expose.sql",
            )
        ):
            sql = ROOT / "supabase" / "experiments" / "relay-spike" / filename
            statements.extend(
                line
                for line in sql.read_text().splitlines()
                if line.strip().lower() not in ("begin;", "commit;")
            )
        if args.action == "schedule-cleanup":
            statements.extend(schedule_statements())
        elif args.action == "unschedule-cleanup":
            statements.extend(schedule_statements(remove=True))
        statements.append("commit;")
        result = subprocess.run(
            [
                "psql",
                "-h",
                "127.0.0.1",
                "-p",
                "55322",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input="\n".join(statements),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        private_write(STATE / (args.action + ".log"), result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(
                f"Local SQL rolled back; inspect protected .oshepherd/local/{args.action}.log"
            )
        messages = {
            "apply-spike": "Isolated spike SQL installed; no production migration history changed",
            "schedule-cleanup": "Local bounded reaper/purge schedules installed; Storage deletion still needs the maintenance runner",
            "unschedule-cleanup": "Local oshepherd spike schedules removed; unrelated Cron jobs preserved",
        }
        print(messages[args.action])
        return
    print(f"Supabase {args.action}: isolated local project on port 55321", flush=True)
    # Start emits a human-readable summary, including when already running.
    # Always obtain credentials from a separate explicit JSON status command.
    actions = ("start", "status") if args.action == "start" else ("status",)
    for action in actions:
        command = (
            ["supabase", "start", "-x", EXCLUDED]
            if action == "start"
            else ["supabase", "status", "--output", "json"]
        )
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True
        )
        private_write(STATE / f"{action}.log", result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(
                f"Local Supabase {action} failed; inspect the protected .oshepherd/local/{action}.log"
            )
    connection = connection_from_output(result.stdout)
    if not connection:
        raise SystemExit(
            "Local Supabase status did not return connection JSON; inspect the protected .oshepherd/local/status.log"
        )
    if connection.get("API_URL") != "http://127.0.0.1:55321":
        raise SystemExit("CLI did not return the expected isolated local origin")
    selected = {
        key: connection[key]
        for key in (
            "API_URL",
            "PUBLISHABLE_KEY",
            "SECRET_KEY",
            "ANON_KEY",
            "SERVICE_ROLE_KEY",
        )
    }
    private_write(STATE / "connection.json", json.dumps(selected))
    print(
        "Local stack ready; credentials saved in protected .oshepherd/local/connection.json"
    )


if __name__ == "__main__":
    main()
