"""Separate, single-operator installer control service and durable work runner.

The browser queues work; a persistent installer process runs the shared operator
engine. No provisioning task is attached to an inference gateway invocation.
"""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .installer_store import InstallerStore
from .management import SupabaseManagement
from .oauth import SupabaseOAuth
from .operator import Operator, OperatorConfig, installation_lock
from .private_files import read_private_json, write_private_json
from .releases import OperatorError
from .releases import database_environment


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class InstallerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str
    operator_subject: UUID
    login_secret: SecretStr = Field(repr=False, min_length=32)
    encryption_key: SecretStr = Field(repr=False)
    oauth_client_id: str = Field(min_length=1, max_length=200)
    oauth_client_secret: SecretStr = Field(repr=False)
    # A broad OAuth consent grant never grants authority over every project.
    allowed_projects: dict[str, str] = Field(min_length=1, max_length=64)
    state_dir: Path
    deploy_gateway: bool = True
    resume_seconds: int = Field(default=3600, ge=300, le=86400)

    @model_validator(mode="after")
    def validate_service(self):
        url = urlsplit(self.origin)
        if (
            url.username
            or url.password
            or url.query
            or url.fragment
            or url.path
            or not url.hostname
            or url.scheme not in ("http", "https")
        ):
            raise ValueError("Installer needs a fixed origin")
        if url.scheme == "http" and url.hostname != "127.0.0.1":
            raise ValueError("Only loopback development may use HTTP")
        if url.port is not None and not 1024 <= url.port <= 65535:
            raise ValueError("Invalid installer port")
        if any(
            not re.fullmatch(r"[a-z]{20}", ref) or not org or len(org) > 100
            for ref, org in self.allowed_projects.items()
        ):
            raise ValueError("Explicit project/organization bindings required")
        return self

    @classmethod
    def load(cls, filename):
        return cls.model_validate(read_private_json(filename))


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_ref: str = Field(pattern=r"^[a-z]{20}$")
    organization_id: str = Field(min_length=1, max_length=100)
    owner_subject: UUID
    database_url: SecretStr = Field(repr=False)


class Installer:
    def __init__(
        self,
        config,
        *,
        oauth=None,
        management_factory=SupabaseManagement,
        operator_factory=Operator,
        clock=time.time,
    ):
        self.config = config
        self.clock = clock
        self.store = InstallerStore(
            config.state_dir / "vault",
            config.encryption_key.get_secret_value(),
            clock=clock,
        )
        self.oauth = oauth or SupabaseOAuth(
            config.oauth_client_id,
            config.oauth_client_secret.get_secret_value(),
            config.origin + "/oauth/callback",
        )
        self.management_factory = management_factory
        self.operator_factory = operator_factory
        self.grant_key = "grant-" + digest(str(config.operator_subject))

    def session(self, request, *, mutation=False):
        token = request.cookies.get("oshepherd_installer", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise OperatorError("installer_login_required")
        key = "session-" + digest(token)
        with self.store.lock():
            value = self.store.load(key)
        if value["operator_subject"] != str(self.config.operator_subject):
            raise OperatorError("installer_login_required")
        if mutation and (
            request.headers.get("Origin") != self.config.origin
            or not hmac.compare_digest(
                digest(request.headers.get("X-CSRF-Token", "")), digest(value["csrf"])
            )
        ):
            raise OperatorError("installer_csrf_failed")
        return key, value

    async def access_token(self):
        with self.store.lock():
            grant = self.store.load(self.grant_key)
            if grant["operator_subject"] != str(self.config.operator_subject):
                raise OperatorError("installer_grant_owner_mismatch")
            if grant.get("refreshing"):
                raise OperatorError("oauth_refresh_ambiguous_reauthorize")
            if grant["access_expires_at"] <= self.clock() + 30:
                # Persist intent before consuming a rotating refresh token.
                grant["refreshing"] = True
                self.store.save(self.grant_key, grant, expires_at=grant["retain_until"])
                tokens = await self.oauth.refresh(grant["refresh_token"])
                grant.update(
                    tokens.private_values(),
                    refreshing=False,
                    access_expires_at=self.clock() + tokens.expires_in,
                )
                self.store.save(self.grant_key, grant, expires_at=grant["retain_until"])
            return grant["access_token"]

    async def projects(self):
        provider = self.management_factory(await self.access_token())
        try:
            return [
                project
                for project in await provider.projects()
                if self.config.allowed_projects.get(project["id"])
                == project["organization_id"]
            ]
        finally:
            await provider.close()

    async def select(self, selection):
        ref = selection.project_ref
        if self.config.allowed_projects.get(ref) != selection.organization_id:
            raise OperatorError("installer_target_not_authorized")
        provider = self.management_factory(await self.access_token())
        try:
            config = await provider.configuration(
                ref,
                selection.organization_id,
                selection.database_url.get_secret_value(),
            )
        finally:
            await provider.close()
        if config.project_ref != ref or config.profile != "edge_free":
            raise OperatorError("installer_target_mismatch")
        database_environment(config.database_url.get_secret_value(), ref)
        key = "installation-" + ref
        with self.store.lock():
            try:
                previous = self.store.load(key)
            except OperatorError as error:
                if str(error) != "installer_record_missing":
                    raise
                previous = None
            if previous:
                self.record_owner(previous)
                if previous["owner_subject"] != str(selection.owner_subject):
                    raise OperatorError("installation_owner_mismatch")
            private = config.model_dump(mode="json")
            for name in ("publishable_key", "secret_key", "database_url"):
                private[name] = getattr(config, name).get_secret_value()
            if previous:
                if previous["queued"] or previous["stage"] in (
                    "configured",
                    "gateway_ready",
                ):
                    return self.public_status(previous)
                previous.update(
                    configuration=private,
                    queued=True,
                    error=None,
                    credential_expires_at=self.clock() + self.config.resume_seconds,
                )
                self.store.save(key, previous, expires_at=253402300799)
                return self.public_status(previous)
            record = {
                "project_ref": ref,
                "organization_id": selection.organization_id,
                "operator_subject": str(self.config.operator_subject),
                "owner_subject": str(selection.owner_subject),
                "queued": True,
                "stage": "authorized",
                "error": None,
                "configuration": private,
                "credential_expires_at": self.clock() + self.config.resume_seconds,
            }
            self.store.save(key, record, expires_at=253402300799)
            return self.public_status(record)

    @staticmethod
    def public_status(record):
        return {
            key: record[key] for key in ("project_ref", "stage", "queued", "error")
        } | {
            "cloud_ready": bool(record.get("cloud_ready", False)),
            "worker_connected": False,
            "inference_verified": False,
            "hosted_qualified": False,
            "next_action": (
                "enroll_worker"
                if record["stage"] == "gateway_ready"
                else (
                    "deploy_gateway_and_enroll_worker"
                    if record["stage"] == "configured"
                    else "resume_setup"
                )
            ),
        }

    def record_owner(self, record):
        if record["operator_subject"] != str(self.config.operator_subject):
            raise OperatorError("installer_record_owner_mismatch")

    async def work_once(self):
        # A separate persistent process owns this lock. Browser traffic and
        # OAuth remain available during long SQL operations.
        with installation_lock(self.config.state_dir / "runner"):
            with self.store.lock():
                self.store.purge()
                records = [
                    (path.stem, self.store.load(path.stem))
                    for path in sorted(self.store.directory.glob("installation-*.json"))
                ]
                for key, record in records:
                    if (
                        record.get("configuration")
                        and record["credential_expires_at"] <= self.clock()
                    ):
                        record.pop("configuration")
                        record.update(
                            queued=False, error="installer_credentials_expired_reselect"
                        )
                        self.store.save(key, record, expires_at=253402300799)
            for key, record in records:
                if not record["queued"]:
                    continue
                ref = record["project_ref"]
                if (
                    record["operator_subject"] != str(self.config.operator_subject)
                    or self.config.allowed_projects.get(ref)
                    != record["organization_id"]
                ):
                    raise OperatorError("installer_target_not_authorized")
                operator = None
                try:
                    # Recheck live provider authority before any project mutation.
                    provider = self.management_factory(await self.access_token())
                    try:
                        await provider.project(ref, record["organization_id"])
                    finally:
                        await provider.close()
                    directory = self.config.state_dir / "operators" / ref
                    filename = directory / "connection.json"
                    if record.get("configuration"):
                        if record["credential_expires_at"] <= self.clock():
                            raise OperatorError(
                                "installer_credentials_expired_reselect"
                            )
                        config = OperatorConfig.model_validate(record["configuration"])
                        write_private_json(filename, record["configuration"])
                    else:
                        config = OperatorConfig.load(filename)
                    if config.project_ref != ref:
                        raise OperatorError("installer_target_mismatch")
                    operator = self.operator_factory(config, directory)
                    result = await operator.install(UUID(record["owner_subject"]))
                    record.update(stage=result["stage"], queued=False, error=None)
                    record.pop("configuration", None)
                    if self.config.deploy_gateway:
                        from .edge_deployment import EdgeDeployment

                        provider = self.management_factory(await self.access_token())
                        deployment = EdgeDeployment(
                            provider, config, directory, record["organization_id"]
                        )
                        try:
                            await deployment.deploy()
                            record.update(stage="gateway_ready", cloud_ready=True)
                        finally:
                            await deployment.close()
                            await provider.close()
                except OperatorError as error:
                    record.update(queued=False, cloud_ready=False, error=str(error))
                    # Stable operator error codes only; raw provider/SQL output
                    # and exception messages are never browser diagnostics.
                    if not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", record["error"]):
                        record["error"] = "installer_setup_failed"
                except Exception:
                    record.update(
                        queued=False, cloud_ready=False, error="installer_setup_failed"
                    )
                finally:
                    if operator:
                        await operator.close()
                with self.store.lock():
                    self.store.save(key, record, expires_at=253402300799)
                return self.public_status(record)
            return None

    async def readiness(self, ref):
        if ref not in self.config.allowed_projects:
            raise OperatorError("installer_target_not_authorized")
        with self.store.lock():
            record = self.store.load("installation-" + ref)
        self.record_owner(record)
        if record["stage"] not in ("configured", "gateway_ready"):
            return self.public_status(record)
        directory = self.config.state_dir / "operators" / ref
        config = OperatorConfig.load(directory / "connection.json")
        if config.project_ref != ref:
            raise OperatorError("installer_target_mismatch")
        operator = self.operator_factory(config, directory)
        try:
            info = await operator.doctor()
            return self.public_status(record) | {
                "cloud_ready": bool(
                    info["enabled"]
                    and info["schema_matches"]
                    and info["gateway_available"]
                ),
                "worker_connected": info["online_workers"] > 0,
                "inference_verified": False,
            }
        finally:
            await operator.close()

    def operator(self, ref):
        if ref not in self.config.allowed_projects:
            raise OperatorError("installer_target_not_authorized")
        with self.store.lock():
            record = self.store.load("installation-" + ref)
        self.record_owner(record)
        if record["stage"] not in ("configured", "gateway_ready"):
            raise OperatorError("installation_not_configured")
        directory = self.config.state_dir / "operators" / ref
        config = OperatorConfig.load(directory / "connection.json")
        if config.project_ref != ref:
            raise OperatorError("installer_target_mismatch")
        return self.operator_factory(config, directory)


async def read_json(request):
    data = bytearray()
    async with asyncio.timeout(10):
        async for chunk in request.stream():
            if len(data) + len(chunk) > 16384:
                raise OperatorError("installer_request_too_large")
            data.extend(chunk)
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise OperatorError("invalid_installer_request") from None


def create_installer_app(config, *, installer=None):
    service = installer or Installer(config)

    @asynccontextmanager
    async def lifespan(_):
        yield
        await service.oauth.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.installer = service

    @app.middleware("http")
    async def boundary(request, call_next):
        if str(request.base_url).rstrip("/") != config.origin:
            return JSONResponse({"error": "installer_origin_mismatch"}, status_code=400)
        try:
            response = await call_next(request)
        except (OperatorError, ValueError, TimeoutError) as error:
            code = (
                str(error)
                if isinstance(error, OperatorError)
                else "invalid_installer_request"
            )
            status = (
                401
                if code
                in (
                    "installer_login_required",
                    "installer_record_expired",
                    "installer_record_missing",
                )
                else 400
            )
            if code == "installation_busy":
                status = 409
            response = JSONResponse({"error": code}, status_code=status)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'",
            }
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return INSTALLER_HTML

    @app.get("/installer.js")
    async def script():
        from fastapi.responses import Response

        return Response(INSTALLER_JS, media_type="application/javascript")

    @app.post("/login")
    async def login(request: Request):
        if request.headers.get("Origin") != config.origin:
            raise OperatorError("installer_csrf_failed")
        values = await read_json(request)
        supplied = values.get("secret", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(
            digest(supplied), digest(config.login_secret.get_secret_value())
        ):
            raise OperatorError("installer_login_required")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with service.store.lock():
            service.store.purge()
            service.store.save(
                "session-" + digest(token),
                {
                    "operator_subject": str(config.operator_subject),
                    "csrf": csrf,
                },
                expires_at=service.clock() + config.resume_seconds,
            )
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "oshepherd_installer",
            token,
            httponly=True,
            secure=config.origin.startswith("https://"),
            samesite="lax",
            max_age=config.resume_seconds,
            path="/",
        )
        return response

    @app.get("/session")
    async def session(request: Request):
        _, value = service.session(request)
        return {"csrf": value["csrf"]}

    @app.post("/logout")
    async def logout(request: Request):
        key, _ = service.session(request, mutation=True)
        with service.store.lock():
            service.store.delete(key)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("oshepherd_installer", path="/")
        return response

    @app.post("/oauth/start")
    async def oauth_start(request: Request):
        session_key, _ = service.session(request, mutation=True)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with service.store.lock():
            service.store.purge()
            service.store.save(
                "flow-" + digest(state),
                {
                    "session_key": session_key,
                    "verifier": verifier,
                },
                expires_at=service.clock() + 300,
            )
        return {"authorize_url": service.oauth.authorize_url(state, verifier)}

    @app.get("/oauth/callback")
    async def callback(request: Request):
        session_key, _ = service.session(request)
        state = request.query_params.get("state", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
            raise OperatorError("invalid_oauth_state")
        key = "flow-" + digest(state)
        with service.store.lock():
            flow = service.store.load(key)
            if flow["session_key"] != session_key:
                raise OperatorError("oauth_session_mismatch")
            # Consume before network I/O: an ambiguous exchange requires a new
            # authorization, and replay cannot consume the code twice.
            service.store.delete(key)
        if "error" in request.query_params:
            raise OperatorError("oauth_consent_denied")
        code = request.query_params.get("code", "")
        if not 1 <= len(code) <= 2048:
            raise OperatorError("invalid_oauth_code")
        tokens = await service.oauth.redeem(code, flow["verifier"])
        retain = service.clock() + config.resume_seconds
        with service.store.lock():
            service.store.save(
                service.grant_key,
                tokens.private_values()
                | {
                    "operator_subject": str(config.operator_subject),
                    "access_expires_at": service.clock() + tokens.expires_in,
                    "retain_until": retain,
                    "refreshing": False,
                },
                expires_at=retain,
            )
        return RedirectResponse("/", status_code=303)

    @app.get("/projects")
    async def projects(request: Request):
        service.session(request)
        return {"projects": await service.projects()}

    @app.post("/installations")
    async def select(request: Request):
        service.session(request, mutation=True)
        selection = Selection.model_validate(await read_json(request))
        return JSONResponse(await service.select(selection), status_code=202)

    @app.get("/installations/{project_ref}")
    async def status(project_ref: str, request: Request):
        service.session(request)
        if project_ref not in config.allowed_projects:
            raise OperatorError("installer_target_not_authorized")
        with service.store.lock():
            record = service.store.load("installation-" + project_ref)
        service.record_owner(record)
        return service.public_status(record)

    @app.get("/installations")
    async def installations(request: Request):
        service.session(request)
        with service.store.lock():
            records = [
                service.store.load(path.stem)
                for path in service.store.directory.glob("installation-*.json")
            ]
        return {
            "installations": [
                service.public_status(record)
                for record in records
                if record["operator_subject"] == str(config.operator_subject)
                and config.allowed_projects.get(record["project_ref"])
                == record["organization_id"]
            ]
        }

    @app.post("/installations/{project_ref}/resume")
    async def resume(project_ref: str, request: Request):
        service.session(request, mutation=True)
        if project_ref not in config.allowed_projects:
            raise OperatorError("installer_target_not_authorized")
        with service.store.lock():
            key = "installation-" + project_ref
            record = service.store.load(key)
            service.record_owner(record)
            record.update(queued=True, error=None)
            service.store.save(key, record, expires_at=253402300799)
        return JSONResponse(service.public_status(record), status_code=202)

    @app.get("/installations/{project_ref}/readiness")
    async def readiness(project_ref: str, request: Request):
        service.session(request)
        return await service.readiness(project_ref)

    @app.post("/installations/{project_ref}/enroll")
    async def enroll(project_ref: str, request: Request):
        service.session(request, mutation=True)
        data = await read_json(request)
        if (
            set(data) - {"name", "capacity", "renew"}
            or not isinstance(data.get("name"), str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", data["name"])
            or type(data.get("capacity")) is not int
            or not 1 <= data["capacity"] <= 64
            or type(data.get("renew", False)) is not bool
        ):
            raise OperatorError("invalid_worker_enrollment")
        operator = service.operator(project_ref)
        try:
            await operator.enroll(
                data["name"], data["capacity"], renew=data.get("renew", False)
            )
        finally:
            await operator.close()
        return {
            "worker_name": data["name"],
            "next_action": "download_worker_files_and_login",
        }

    @app.get("/installations/{project_ref}/workers/{name}/{artifact}")
    async def download(project_ref: str, name: str, artifact: str, request: Request):
        from .operator import Installation
        from .private_files import read_private_json
        from fastapi.responses import Response

        service.session(request)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) or artifact not in (
            "environment",
            "enrollment",
        ):
            raise OperatorError("invalid_worker_artifact")
        operator = service.operator(project_ref)
        try:
            state = Installation.load(operator.directory, operator.config)
            enrollment = state.workers.get(name)
            if not enrollment or enrollment.revocation_requested or enrollment.issuing:
                raise OperatorError("worker_enrollment_unavailable")
            if artifact == "enrollment":
                # Release only the bounded, project-bound one-use challenge.
                from .startup import read_enrollment

                token = read_enrollment(
                    operator.directory / (name + ".enrollment.json"),
                    supabase_url=operator.config.supabase_url,
                )
                data = {
                    "v": 1,
                    "supabase_url": operator.config.supabase_url,
                    "token_hash": token,
                }
                body, filename = json.dumps(data), name + ".enrollment.json"
            else:
                values = {
                    "SUPABASE_URL": operator.config.supabase_url,
                    "SUPABASE_PUBLISHABLE_KEY": operator.config.publishable_key.get_secret_value(),
                    "SUPABASE_SCHEMA": "oshepherd_api",
                    "CONCURRENCY": str(enrollment.capacity),
                    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                    "WORKER_SESSION_FILE": ".oshepherd/workers/"
                    + name
                    + ".worker-session.json",
                }
                body = "".join(
                    f"{key}={json.dumps(value)}\n" for key, value in values.items()
                )
                filename = name + ".worker.env"
            return Response(
                body,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": 'attachment; filename="' + filename + '"'
                },
            )
        finally:
            await operator.close()

    @app.post("/installations/{project_ref}/verify-inference")
    async def verify(project_ref: str, request: Request):
        service.session(request, mutation=True)
        data = await read_json(request)
        if (
            set(data) != {"model"}
            or not isinstance(data["model"], str)
            or not 1 <= len(data["model"]) <= 256
        ):
            raise OperatorError("invalid_model")
        operator = service.operator(project_ref)
        try:
            return await operator.verify_inference(data["model"])
        finally:
            await operator.close()

    @app.delete("/oauth/grant")
    async def forget(request: Request):
        service.session(request, mutation=True)
        with service.store.lock():
            service.store.delete(service.grant_key)
        return {
            "provider_credentials_deleted": True,
            "installed_runtime_unchanged": True,
        }

    return app


INSTALLER_HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Oshepherd setup</title><script src="/installer.js" defer></script>
<body><main><h1>Oshepherd setup</h1><p>Experimental Supabase Edge installation.
Use a dedicated Free project. No paid upgrades or model downloads.</p>
<form id="login"><label>Operator access code <input name="secret" type="password" required autocomplete="off"></label><button>Sign in</button></form>
<button id="connect" hidden>Connect Supabase</button>
<form id="setup" hidden><label>Project <select name="project" required></select></label>
<p>Setup adds Oshepherd schemas, private result storage and cleanup schedules.
Prompts and results pass through your selected project's region.</p>
<label>Existing Auth owner UUID <input name="owner" required></label>
<label>Postgres connection URL <input name="database" type="password" required autocomplete="off"></label>
<button>Configure installation</button></form><pre id="status" role="status"></pre>
<button id="resume" hidden>Resume setup</button>
<form id="enroll" hidden><h2>Connect your Ollama worker</h2>
<label>Worker name <input name="name" required pattern="[a-z][a-z0-9-]{0,31}" value="workstation"></label>
<label>Capacity <input name="capacity" type="number" min="1" max="64" value="2" required></label>
<label><input name="renew" type="checkbox">Reissue a one-use challenge</label><button>Create worker files</button></form>
<p id="downloads" hidden><a id="environment">Download worker environment</a> · <a id="challenge">Download enrollment</a>
<br>Transfer both files privately to the Ollama machine and follow the worker login/start commands in the operator runbook.</p>
<form id="inference" hidden><label>Installed model <input name="model" required></label><button>Verify inference (up to 8 tokens)</button></form>
<button id="forget" hidden>Delete temporary provider access</button>
<button id="logout" hidden>Sign out</button></main></body></html>"""

INSTALLER_JS = """'use strict';
let csrf = '', projects = [], selected = '';
const el = id => document.getElementById(id);
const show = value => { el('status').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body: body ? JSON.stringify(body) : undefined});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Setup failed'); return result;
}
async function connected() {
  el('login').hidden = true; el('connect').hidden = false; el('logout').hidden = false;
  try { projects = (await api('/projects')).projects;
    el('setup').hidden = false; el('forget').hidden = false;
    el('setup').elements.project.replaceChildren(...projects.map(p => {
      const option = document.createElement('option'); option.value=p.id;
      option.textContent=`${p.name} — ${p.region} (${p.status})`; return option;
    }));
    const records = (await api('/installations')).installations;
    if (records.length) { selected = records[0].project_ref; el('setup').elements.project.value=selected; await poll(); }
  } catch (_) { show('Connect Supabase to select an authorized project.'); }
}
el('login').onsubmit = async event => { event.preventDefault();
  try { csrf = (await api('/login','POST',{secret:el('login').elements.secret.value})).csrf;
    el('login').reset(); await connected(); } catch (error) { show(error.message); }
};
el('connect').onclick = async () => { try { location.assign((await api('/oauth/start','POST')).authorize_url); } catch (error) { show(error.message); } };
async function poll() { const reference=selected; try { const value = await api('/installations/'+reference); if (reference !== selected) return; show(value);
  el('resume').hidden = value.queued || (!value.error && value.stage === 'gateway_ready');
  if (value.queued) setTimeout(() => { if(reference === selected) poll(); },2000);
  else if (!value.error) { const ready=await api('/installations/'+reference+'/readiness'); if(reference !== selected) return; show(ready); }
  el('enroll').hidden = !['configured','gateway_ready'].includes(value.stage);
  el('inference').hidden = el('enroll').hidden;
  } catch (error) { show(error.message); } }
el('setup').onsubmit = async event => { event.preventDefault(); const form=el('setup').elements;
  const project=projects.find(p => p.id === form.project.value); if (!project) return;
  try { selected=project.id; await api('/installations','POST',{project_ref:selected,organization_id:project.organization_id,owner_subject:form.owner.value,database_url:form.database.value});
    form.database.value=''; await poll(); } catch (error) { show(error.message); }
};
el('setup').elements.project.onchange = async () => {
  selected=el('setup').elements.project.value; el('downloads').hidden=true; el('enroll').hidden=true; el('inference').hidden=true; el('resume').hidden=true;
  await poll();
};
el('resume').onclick = async () => { try { await api('/installations/'+selected+'/resume','POST'); await poll(); } catch (error) { show(error.message); } };
el('enroll').onsubmit = async event => { event.preventDefault(); const form=el('enroll').elements;
  const reference=selected, name=form.name.value;
  try { await api('/installations/'+reference+'/enroll','POST',{name,capacity:Number(form.capacity.value),renew:form.renew.checked});
    if(reference !== selected) return; const base='/installations/'+reference+'/workers/'+name+'/';
    el('environment').href=base+'environment'; el('challenge').href=base+'enrollment'; el('downloads').hidden=false;
    show('Keep both downloaded files private (chmod 600) before worker login.'); } catch (error) { show(error.message); } };
el('inference').onsubmit = async event => { event.preventDefault();
  try { show(await api('/installations/'+selected+'/verify-inference','POST',{model:el('inference').elements.model.value})); } catch (error) { show(error.message); } };
el('forget').onclick = async () => { try { show(await api('/oauth/grant','DELETE')); } catch (error) { show(error.message); } };
el('logout').onclick = async () => { try { await api('/logout','POST'); location.assign('/'); } catch (error) { show(error.message); } };
api('/session').then(async value => { csrf=value.csrf; await connected(); }).catch(() => {});
"""
