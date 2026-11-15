"""Verified, wheel-contained release assets and transactional migration runner."""

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import zipfile
from urllib.parse import parse_qs, unquote, urlsplit


class OperatorError(RuntimeError):
    """Credential-free error codes, safe for a CLI diagnostic."""


def migration_path(manifest, installed):
    """Accept only a prefix of an explicitly supported, immutable history."""
    entries = {entry["version"]: entry for entry in manifest["migrations"]}
    for path in manifest["migration_paths"]:
        expected = [
            {"version": version, "sha256": entries[version]["content_sha256"]}
            for version in path
        ]
        if installed == expected[: len(installed)]:
            return [entries[version] for version in path]
    raise OperatorError("oshepherd_migration_conflict")


SCHEDULES = {
    "oshepherd-reap-v1": (
        "10 seconds",
        "set statement_timeout='5s'; set lock_timeout='100ms'; select oshepherd.scheduled_sweep('reap');",
    ),
    "oshepherd-purge-v1": (
        "* * * * *",
        "set statement_timeout='5s'; set lock_timeout='100ms'; select oshepherd.scheduled_sweep('purge'); "
        "delete from cron.job_run_details where runid in(select d.runid from cron.job_run_details d "
        "join cron.job j on j.jobid=d.jobid where j.jobname in('oshepherd-reap-v1','oshepherd-purge-v1') "
        "and j.username=current_user and d.end_time<clock_timestamp()-interval '2 days' order by d.runid limit 256);",
    ),
}


def release_assets():
    root = files("oshepherd.backends.supabase").joinpath("release")
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    artifacts = {}
    for entry in manifest["migrations"]:
        artifacts[entry["path"]] = entry["sha256"]
    artifacts.update(manifest["artifacts"])
    for name, expected in artifacts.items():
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise OperatorError("invalid_release_manifest")
        if hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() != expected:
            raise OperatorError("release_hash_mismatch")
    package = files("oshepherd")
    for name, expected in manifest["python_sources"].items():
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise OperatorError("invalid_release_manifest")
        if hashlib.sha256(package.joinpath(name).read_bytes()).hexdigest() != expected:
            raise OperatorError("release_hash_mismatch")
    return root, manifest


def database_environment(database_url, project_ref, *, local_port=55322):
    parsed = urlsplit(database_url)
    query = parse_qs(parsed.query, strict_parsing=True)
    if (
        parsed.scheme not in ("postgres", "postgresql")
        or parsed.fragment
        or not parsed.hostname
        or not parsed.username
        or not parsed.password
    ):
        raise OperatorError("invalid_database_connection")
    if set(query) - {"sslmode"} or len(query.get("sslmode", [])) > 1:
        raise OperatorError("unsupported_database_option")
    username = unquote(parsed.username)
    if project_ref == "local":
        if (
            parsed.hostname != "127.0.0.1"
            or not 1024 <= local_port <= 65535
            or parsed.port != local_port
            or username not in ("postgres", "supabase_admin")
            or parsed.path != "/postgres"
        ):
            raise OperatorError("database_target_mismatch")
        sslmode = "disable"
    else:
        direct = (
            parsed.hostname == f"db.{project_ref}.supabase.co"
            and username == "postgres"
        )
        pooled = (
            parsed.hostname.endswith(".pooler.supabase.com")
            and username == f"postgres.{project_ref}"
        )
        if not (direct or pooled) or parsed.path != "/postgres":
            raise OperatorError("database_target_mismatch")
        sslmode = query.get("sslmode", ["require"])[0]
        if sslmode not in ("require", "verify-full"):
            raise OperatorError("database_tls_required")
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith("PG")},
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": username,
        "PGPASSWORD": unquote(parsed.password),
        "PGDATABASE": "postgres",
        "PGSSLMODE": sslmode,
        "PGCONNECT_TIMEOUT": "10",
    }


class Database:
    def __init__(self, config, *, runner=subprocess.run):
        if config.database_url is None:
            raise OperatorError("database_connection_required")
        self.env = database_environment(
            config.database_url.get_secret_value(),
            config.project_ref,
            local_port=getattr(config, "local_database_port", 55322),
        )
        self.runner = runner
        self.local_role_upgrade = (
            config.project_ref == "local" and self.env["PGUSER"] == "supabase_admin"
        )
        self.exposed_schemas = getattr(config, "exposed_schemas", None)
        if config.project_ref != "local" and self.exposed_schemas is None:
            raise OperatorError("existing_exposed_schemas_required")

    def execute(self, sql):
        # Neither credentials nor raw SQL/psql output appear in exceptions/logs.
        try:
            result = self.runner(
                ["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=terse"],
                input=sql,
                text=True,
                capture_output=True,
                env=self.env,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise OperatorError("database_unavailable") from None
        if result.returncode:
            known = (
                "oshepherd_resource_collision",
                "oshepherd_schema_drift",
                "oshepherd_migration_conflict",
                "oshepherd_schedule_collision",
                "oshepherd_bucket_mismatch",
                "oshepherd_exposed_schema_missing",
            )
            # CONTEXT can contain the whole migration, including unrelated
            # exception literals. Only classify an exact primary ERROR message.
            primary = re.search(r"^.*?ERROR:\s*(.*?)\s*$", result.stderr, re.MULTILINE)
            code = (
                primary.group(1)
                if primary and primary.group(1) in known
                else "migration_failed"
            )
            raise OperatorError(code)
        return result.stdout.strip()

    def migrate(self):
        root, manifest = release_assets()
        # GRANT can succeed with only a warning when the caller lacks grant
        # options. Reject that role before recording a partially effective setup.
        if (
            self.execute(
                "select pg_catalog.has_schema_privilege(current_user,'storage','USAGE WITH GRANT OPTION');"
            )
            != "t"
        ):
            raise OperatorError("oshepherd_database_privileges_required")
        installed = []
        if (
            self.execute(
                "select pg_catalog.to_regclass('oshepherd.release_migrations') is not null;"
            )
            == "t"
        ):
            installed = json.loads(
                self.execute(
                    "select coalesce(json_agg(json_build_object('version',version,'sha256',sha256) order by version),'[]'::json) from oshepherd.release_migrations;"
                )
            )
        selected = migration_path(manifest, installed)
        versions = ",".join("'" + entry["version"] + "'" for entry in selected)
        for entry in selected:
            version, digest = entry["version"], entry["content_sha256"]
            sql = root.joinpath(entry["path"]).read_text()
            name = Path(entry["path"]).stem.split("_", 1)[1]
            if (
                not re.fullmatch(r"[0-9]{14}", version)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or "$release_sql$" in sql
            ):
                raise OperatorError("invalid_release_manifest")
            self.execute(f"""begin;
set local statement_timeout='50s'; set local lock_timeout='10s';
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
{self.schema_exposure_sql()}
do $apply$ declare installed text; expected text; actual text; begin
  if pg_catalog.to_regclass('oshepherd.release_migrations') is not null then
    if exists(select 1 from oshepherd.release_migrations where version not in({versions})) then
      raise exception 'oshepherd_migration_conflict'; end if;
    select fingerprint into expected from oshepherd.release_migrations order by version desc limit 1;
    if expected is null or expected <> oshepherd.schema_fingerprint() then
      raise exception 'oshepherd_schema_drift'; end if;
    select sha256 into installed from oshepherd.release_migrations where version='{version}';
    if installed is not null and installed <> '{digest}' then raise exception 'oshepherd_migration_conflict'; end if;
  end if;
  if installed is null then execute $release_sql${sql}$release_sql$; end if;
  if not exists(select 1 from storage.buckets where id='oshepherd-results' and not public and file_size_limit=33554432
    and allowed_mime_types=array['application/json']::text[]) then raise exception 'oshepherd_bucket_mismatch'; end if;
  create schema if not exists supabase_migrations;
  create table if not exists supabase_migrations.schema_migrations(version text primary key,statements text[],name text);
  if exists(select 1 from supabase_migrations.schema_migrations where version='{version}' and name is distinct from '{name}') then
    raise exception 'oshepherd_migration_conflict'; end if;
  insert into supabase_migrations.schema_migrations(version,statements,name)
    values('{version}',array['-- Oshepherd manifest content SHA256: {digest}'],'{name}') on conflict(version) do nothing;
end $apply$;
commit;""")
        return manifest

    def schema_exposure_sql(self):
        # Provider-configured schemas need explicit discovery outside SQL. Never
        # assume a hosted project's API configuration has only default schemas.
        supplied = ",".join(self.exposed_schemas or [])
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name)
            for name in self.exposed_schemas or []
        ):
            raise OperatorError("invalid_exposed_schemas")
        return f"""do $exposed$ declare global_cfg text; database_cfg text; merged text; schema_name text; changed boolean:=false; begin
 select pg_catalog.substr(setting,18) into global_cfg from pg_catalog.pg_db_role_setting s
  cross join lateral pg_catalog.unnest(s.setconfig) setting
  where s.setrole=(select oid from pg_catalog.pg_roles where rolname='authenticator') and s.setdatabase=0 and setting like 'pgrst.db_schemas=%';
 select pg_catalog.substr(setting,18) into database_cfg from pg_catalog.pg_db_role_setting s
  cross join lateral pg_catalog.unnest(s.setconfig) setting
  where s.setrole=(select oid from pg_catalog.pg_roles where rolname='authenticator')
   and s.setdatabase=(select oid from pg_catalog.pg_database where datname=pg_catalog.current_database()) and setting like 'pgrst.db_schemas=%';
 select pg_catalog.string_agg(name,',' order by first_position) into merged from (
  select pg_catalog.btrim(name) name,min(position) first_position from pg_catalog.unnest(
   pg_catalog.string_to_array(coalesce(database_cfg,global_cfg,nullif('{supplied}',''),'public,graphql_public'),',') ||
   pg_catalog.string_to_array(coalesce(global_cfg,''),',') || pg_catalog.string_to_array('{supplied}',',') || array['oshepherd_api'])
   with ordinality names(name,position) where pg_catalog.btrim(name)<>'' group by pg_catalog.btrim(name)) schemas;
 foreach schema_name in array pg_catalog.string_to_array(merged,',') loop
  if schema_name<>'oshepherd_api' and not exists(select 1 from pg_catalog.pg_namespace where nspname=schema_name) then
   raise exception 'oshepherd_exposed_schema_missing'; end if;
 end loop;
 if global_cfg is distinct from merged then
  execute pg_catalog.format('alter role authenticator set pgrst.db_schemas=%L',merged); changed:=true; end if;
 if database_cfg is not null and database_cfg is distinct from merged then
  execute pg_catalog.format('alter role authenticator in database %I set pgrst.db_schemas=%L',pg_catalog.current_database(),merged); changed:=true; end if;
 if changed then perform pg_catalog.pg_notify('pgrst','reload config'); perform pg_catalog.pg_notify('pgrst','reload schema'); end if;
end $exposed$;"""

    def schedules(self, *, remove=False):
        commands = SCHEDULES
        owners = (
            "username in(current_user,'postgres')"
            if self.local_role_upgrade
            else "username=current_user"
        )
        statements = [
            "begin; select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schedules_v1',0));"
        ]
        if not remove:
            statements.append("create extension if not exists pg_cron;")
        for name, (interval, command) in commands.items():
            quoted = "'" + command.replace("'", "''") + "'"
            statements.append(
                f"do $$ begin if exists(select 1 from cron.job where jobname='{name}' and {owners} and command<>{quoted}) then raise exception 'oshepherd_schedule_collision'; end if; end $$;"
            )
            if self.local_role_upgrade and not remove:
                statements.append(
                    f"select cron.unschedule(jobid) from cron.job where jobname='{name}' and username='postgres' and command={quoted};"
                )
            statements.append(
                f"select cron.unschedule(jobid) from cron.job where jobname='{name}' and {owners};"
                if remove
                else f"select cron.schedule('{name}','{interval}',{quoted});"
            )
        statements.append("commit;")
        self.execute("\n".join(statements))


def export_bundle(directory):
    root, manifest = release_assets()
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise OperatorError("bundle_directory_not_empty")
    directory.mkdir(parents=True, exist_ok=True)
    for entry in manifest["migrations"]:
        name = "supabase/" + entry["path"]
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(root.joinpath(entry["path"]).read_bytes())
    for name in manifest["artifacts"]:
        if not name.startswith("functions/"):
            continue
        target = directory / "supabase" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(root.joinpath(name).read_bytes())
    (directory / "supabase/config.toml").write_text(
        'project_id = "oshepherd-release"\n\n[functions.ollama]\nverify_jwt = false\nentrypoint = "./functions/ollama/index.ts"\n'
    )
    (directory / "oshepherd-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return manifest


def export_vercel(directory, wheel):
    root, manifest = release_assets()
    wheel = Path(wheel)
    try:
        with zipfile.ZipFile(wheel) as archive:
            packaged = json.loads(
                archive.read("oshepherd/backends/supabase/release/manifest.json")
            )
            if (
                packaged != manifest
                or "oshepherd/api/factory.py" not in archive.namelist()
            ):
                raise OperatorError("wheel_release_mismatch")
            expected = dict(manifest["artifacts"])
            expected.update(
                {entry["path"]: entry["sha256"] for entry in manifest["migrations"]}
            )
            for name, digest in expected.items():
                if (
                    hashlib.sha256(
                        archive.read("oshepherd/backends/supabase/release/" + name)
                    ).hexdigest()
                    != digest
                ):
                    raise OperatorError("wheel_release_mismatch")
            for name, digest in manifest["python_sources"].items():
                if (
                    hashlib.sha256(archive.read("oshepherd/" + name)).hexdigest()
                    != digest
                ):
                    raise OperatorError("wheel_release_mismatch")
            if any(
                name.startswith(("tests/", "scripts/", ".oshepherd/"))
                or name.endswith((".env", ".worker-session.json"))
                for name in archive.namelist()
            ):
                raise OperatorError("invalid_release_wheel")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        raise OperatorError("invalid_release_wheel") from None
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise OperatorError("bundle_directory_not_empty")
    directory.mkdir(parents=True, exist_ok=True)
    for name in manifest["artifacts"]:
        if name.startswith("templates/vercel/"):
            (directory / Path(name).name).write_bytes(root.joinpath(name).read_bytes())
    (directory / "vendor").mkdir()
    shutil.copyfile(wheel, directory / "vendor" / wheel.name)
    # Root runtime pins are shipped as constraints; no ambient dependency
    # resolver may select a newer transport during a provider build.
    requirements = root.joinpath(
        "templates/vercel/runtime-requirements.txt"
    ).read_text()
    constraints = [
        line for line in requirements.splitlines() if line and not line.startswith("#")
    ]
    from importlib.metadata import version

    dependency = "oshepherd[supabase]==" + version("oshepherd")
    (directory / "pyproject.toml").write_text(
        '[project]\nname="oshepherd-vercel-gateway"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
        + "dependencies=["
        + json.dumps(dependency)
        + "]\n\n[tool.uv.sources]\noshepherd={path="
        + json.dumps("vendor/" + wheel.name)
        + "}\n\n[tool.uv]\nconstraint-dependencies="
        + json.dumps(constraints)
        + '\n\n[tool.vercel]\nentrypoint="app:app"\n'
    )
    (directory / "requirements.txt").write_text(
        "-r runtime-requirements.txt\n./vendor/" + wheel.name + "\n"
    )
    try:
        result = subprocess.run(
            ["uv", "lock", "--offline"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            # A clean machine may not have registry metadata cached. The full
            # runtime constraint list still pins every dependency during fetch.
            result = subprocess.run(
                ["uv", "lock"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=60,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise OperatorError("vercel_lock_failed") from None
    if result.returncode:
        raise OperatorError("vercel_lock_failed")
    (directory / "oshepherd-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return {
        "directory": str(directory.absolute()),
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "contains_credentials": False,
        "hosted_qualified": False,
    }
