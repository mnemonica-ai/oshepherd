"""Production-schema installation/recovery using real isolated Auth/PostgREST."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from oshepherd.backends.supabase.client import RpcClient, RpcError
from oshepherd.backends.supabase.operator import Installation, Operator, OperatorConfig
from oshepherd.backends.supabase.releases import Database, OperatorError
from oshepherd.backends.supabase.sessions import WorkerAuth
from oshepherd.backends.supabase.startup import read_enrollment
from .test_identity import cleanup_cluster


@asynccontextmanager
async def operator_fixture(connection, directory):
    config = OperatorConfig(
        project_ref="local",
        supabase_url=connection["API_URL"],
        publishable_key=connection["PUBLISHABLE_KEY"],
        secret_key=connection["SECRET_KEY"],
        database_url="postgresql://postgres:postgres@127.0.0.1:55322/postgres",
    )
    async with httpx.AsyncClient(timeout=10) as client:
        operator = Operator(config, directory, client=client)
        owner = await operator.auth_request(
            "POST",
            "users",
            json={
                "email": f"operator-test-{uuid4()}@enrollment.invalid",
                "email_confirm": True,
            },
        )
        subjects = [owner["id"]]
        try:
            yield operator, UUID(owner["id"]), subjects
        finally:
            if (directory / "installation.json").exists():
                state = Installation.load(directory, config)
                cleanup_cluster(str(state.cluster_id), schema="oshepherd")
                subjects += [
                    str(worker.auth_subject)
                    for worker in state.workers.values()
                    if worker.auth_subject
                ]
            for subject in set(subjects):
                await operator.auth_request("DELETE", "users/" + subject)


def test_real_install_replay_enroll_upgrade_rotate_revoke_and_preserve_data(
    local_connection, tmp_path
):
    async def run():
        async with operator_fixture(local_connection, tmp_path / "private") as (
            operator,
            owner,
            _,
        ):
            first = await operator.install(owner)
            state = Installation.load(operator.directory, operator.config)
            key, cluster = state.application_key, state.cluster_id
            assert first["stage"] == "configured"
            assert not first["hosted_qualified"] and not first["gateway_deployed"]
            assert await operator.install(owner) == first
            assert (
                Installation.load(operator.directory, operator.config).application_key
                == key
            )
            with pytest.raises(OperatorError, match="installation_owner_mismatch"):
                await operator.install(uuid4())
            readiness = await operator.doctor()
            assert readiness["schema_matches"] and readiness["enabled"]
            assert readiness["online_workers"] == 0
            assert not readiness["inference_verified"]
            challenge = await operator.enroll("test-worker", 2)
            assert await operator.enroll("test-worker", 2) == challenge
            with pytest.raises(OperatorError, match="worker_capacity_conflict"):
                await operator.enroll("test-worker", 3)
            token = read_enrollment(
                challenge["enrollment_file"], supabase_url=operator.config.supabase_url
            )
            auth = WorkerAuth(
                operator.config.supabase_url,
                operator.config.publishable_key.get_secret_value(),
                client=operator.client,
            )
            session = await auth.enroll(token)
            worker = RpcClient(
                operator.config.supabase_url,
                operator.config.publishable_key.get_secret_value(),
                access_token=session.access_token.get_secret_value(),
                client=operator.client,
            )
            registered = await worker.rpc("register_worker", {})
            assert registered["capacity"] == 2 and registered["cluster_id"] == str(
                cluster
            )
            for name in ("installation_info", "disable_cluster", "revoke_api_key"):
                payload = {"cluster_id": str(cluster)}
                if name == "revoke_api_key":
                    payload["key_id"] = str(state.key_id)
                with pytest.raises(RpcError) as denied:
                    await worker.rpc(name, payload)
                assert denied.value.status == 403
            await operator.install(owner)  # Current release upgrade/reconciliation.
            assert (
                Installation.load(operator.directory, operator.config).cluster_id
                == cluster
            )
            await operator.rotate_key()
            updated = Installation.load(operator.directory, operator.config)
            assert updated.application_key != key
            with pytest.raises(RpcError, match="unauthorized"):
                await operator.rpc.rpc(
                    "backend_info",
                    {"cluster_id": str(cluster), "key": key.get_secret_value()},
                )
            assert (await operator.doctor())["schema_matches"]
            await operator.revoke_worker("test-worker")
            with pytest.raises(OperatorError, match="worker_revoked_use_new_name"):
                await operator.enroll("test-worker", 2, renew=True)
            with pytest.raises(RpcError, match="worker_revoked"):
                await worker.rpc("register_worker", {})
            assert (await operator.disable())["data_preserved"]
            assert (await operator.disable())["disabled"]
            assert not (await operator.doctor())["enabled"]
            with pytest.raises(OperatorError, match="installation_disabled"):
                await operator.install(owner)
            for path in operator.directory.iterdir():
                assert path.stat().st_mode & 0o777 == 0o600

    asyncio.run(run())


def test_lost_enrollment_issuance_requires_explicit_renewal(local_connection, tmp_path):
    async def run():
        async with operator_fixture(local_connection, tmp_path / "private") as (
            operator,
            owner,
            _,
        ):
            await operator.install(owner)
            original = operator.auth_request

            async def lost(method, path, **kwargs):
                result = await original(method, path, **kwargs)
                if path == "generate_link":
                    raise OperatorError("simulated_acknowledgement_loss")
                return result

            operator.auth_request = lost
            with pytest.raises(OperatorError, match="simulated_acknowledgement_loss"):
                await operator.enroll("ambiguous-worker", 1)
            before = Installation.load(operator.directory, operator.config).workers[
                "ambiguous-worker"
            ]
            operator.auth_request = original
            with pytest.raises(
                OperatorError, match="enrollment_issuance_ambiguous_use_renew"
            ):
                await operator.enroll("ambiguous-worker", 1)
            challenge = await operator.enroll("ambiguous-worker", 1, renew=True)
            after = Installation.load(operator.directory, operator.config).workers[
                "ambiguous-worker"
            ]
            assert (
                after.worker_id == before.worker_id
                and after.auth_subject == before.auth_subject
            )
            auth = WorkerAuth(
                operator.config.supabase_url,
                operator.config.publishable_key.get_secret_value(),
                client=operator.client,
            )
            session = await auth.enroll(
                read_enrollment(
                    challenge["enrollment_file"],
                    supabase_url=operator.config.supabase_url,
                )
            )
            assert session.user_id == after.auth_subject

    asyncio.run(run())


@pytest.mark.parametrize("lost_at", ["revoke_api_key", "export_configuration"])
def test_key_rotation_resumes_after_revocation_or_configuration_write_loss(
    local_connection, tmp_path, lost_at
):
    async def run():
        async with operator_fixture(local_connection, tmp_path / "private") as (
            operator,
            owner,
            _,
        ):
            await operator.install(owner)
            rpc, export = operator.rpc.rpc, operator.export_configuration

            async def lost_rpc(name, payload, **kwargs):
                result = await rpc(name, payload, **kwargs)
                if name == lost_at:
                    raise OperatorError("simulated_acknowledgement_loss")
                return result

            def lost_export(state):
                raise OperatorError("simulated_acknowledgement_loss")

            if lost_at == "revoke_api_key":
                operator.rpc.rpc = lost_rpc
            else:
                operator.export_configuration = lost_export
            with pytest.raises(OperatorError, match="simulated_acknowledgement_loss"):
                await operator.rotate_key()
            pending = Installation.load(operator.directory, operator.config).rotation
            assert pending is not None
            with pytest.raises(
                OperatorError, match="pending_key_rotation_use_rotate_key"
            ):
                await operator.install(owner)
            operator.rpc.rpc, operator.export_configuration = rpc, export
            await operator.rotate_key()
            state = Installation.load(operator.directory, operator.config)
            assert (
                state.key_id == pending.key_id
                and state.application_key == pending.raw_key
            )
            assert state.rotation is None
            info = await operator.rpc.rpc(
                "installation_info", {"cluster_id": str(state.cluster_id)}
            )
            assert info["active_keys"] == 1

    asyncio.run(run())


def test_lost_bootstrap_and_auth_acknowledgements_reconcile_one_identity(
    local_connection, tmp_path
):
    async def run():
        async with operator_fixture(local_connection, tmp_path / "private") as (
            operator,
            owner,
            subjects,
        ):
            rpc = operator.rpc.rpc
            lost = False

            async def lose_key_response(name, request, **kwargs):
                nonlocal lost
                result = await rpc(name, request, **kwargs)
                if name == "create_api_key" and not lost:
                    lost = True
                    raise OperatorError("simulated_acknowledgement_loss")
                return result

            operator.rpc.rpc = lose_key_response
            with pytest.raises(OperatorError, match="simulated_acknowledgement_loss"):
                await operator.install(owner)
            saved = Installation.load(operator.directory, operator.config)
            operator.rpc.rpc = rpc
            await operator.install(owner)
            after = Installation.load(operator.directory, operator.config)
            assert (
                after.cluster_id == saved.cluster_id
                and after.application_key == saved.application_key
            )
            assert (
                await operator.rpc.rpc(
                    "installation_info", {"cluster_id": str(saved.cluster_id)}
                )
            )["active_keys"] == 1
            auth_request = operator.auth_request
            lost = False

            async def lose_user_response(method, path, **kwargs):
                nonlocal lost
                result = await auth_request(method, path, **kwargs)
                if method == "POST" and path == "users" and not lost:
                    subjects.append(result["id"])
                    lost = True
                    raise OperatorError("simulated_acknowledgement_loss")
                return result

            operator.auth_request = lose_user_response
            with pytest.raises(OperatorError, match="simulated_acknowledgement_loss"):
                await operator.enroll("lost-worker", 1)
            operator.auth_request = auth_request
            challenge = await operator.enroll("lost-worker", 1)
            assert challenge["worker_id"]
            after = Installation.load(operator.directory, operator.config)
            assert str(after.workers["lost-worker"].auth_subject) == subjects[-1]

    asyncio.run(run())


def test_schema_drift_rejected_without_committing_or_losing_exposed_schemas(
    local_connection, tmp_path
):
    async def run():
        async with operator_fixture(local_connection, tmp_path / "private") as (
            operator,
            owner,
            _,
        ):
            await operator.install(owner)
            real = Database(operator.config)
            original = real.runner

            def injected(*args, **kwargs):
                sql = kwargs["input"]
                kwargs["input"] = (
                    "begin; alter table oshepherd.jobs disable row level security;\n"
                    + sql.removeprefix("begin;").removesuffix("commit;")
                    + "rollback;"
                )
                return original(*args, **kwargs)

            drifted = Database(operator.config, runner=injected)
            with pytest.raises(OperatorError, match="oshepherd_schema_drift"):
                await asyncio.to_thread(drifted.migrate)
            assert (await operator.doctor())["schema_matches"]
            # Both production and spike exposure survived the merge.
            output = real.execute(
                "select setting from pg_catalog.pg_db_role_setting s cross join lateral unnest(s.setconfig) setting where setrole=(select oid from pg_roles where rolname='authenticator') and setting like 'pgrst.db_schemas=%';"
            )
            assert (
                "oshepherd_spike_api" in output
                and "oshepherd_api" in output
                and "public" in output
            )

    asyncio.run(run())
