"""Real browserless Auth exchange; no public signup, SMTP or model access."""

import asyncio
import uuid

import httpx
import pytest

from oshepherd.backends.supabase.client import RpcError
from oshepherd.backends.supabase.sessions import WorkerAuth, WorkerSession


@pytest.mark.parametrize("admin_key_mode", ["legacy-jwt", "secret-key"])
def test_operator_generated_enrollment_is_single_use_and_refreshable(
    local_connection, tmp_path, admin_key_mode
):
    async def run():
        origin = local_connection["API_URL"]
        admin_headers = {
            "apikey": local_connection["SERVICE_ROLE_KEY"],
            "Authorization": "Bearer " + local_connection["SERVICE_ROLE_KEY"],
        }
        if admin_key_mode == "secret-key":
            admin_headers = {"apikey": local_connection["SECRET_KEY"]}
        email = f"worker-{uuid.uuid4()}@enrollment.invalid"
        async with httpx.AsyncClient(base_url=origin, timeout=10) as client:
            denied = await client.post(
                "/auth/v1/signup",
                headers={"apikey": local_connection["PUBLISHABLE_KEY"]},
                json={"email": email, "password": str(uuid.uuid4())},
            )
            assert denied.is_error, "Public signup must be disabled"
            created = await client.post(
                "/auth/v1/admin/users",
                headers=admin_headers,
                json={"email": email, "email_confirm": True},
            )
            assert created.status_code in (200, 201), "Operator user creation failed"
            subject = created.json()["id"]
            try:
                link = await client.post(
                    "/auth/v1/admin/generate_link",
                    headers=admin_headers,
                    json={"type": "magiclink", "email": email},
                )
                assert link.status_code == 200, "Operator token generation failed"
                token_hash = link.json()["hashed_token"]
                auth = WorkerAuth(
                    origin, local_connection["PUBLISHABLE_KEY"], client=client
                )
                session = await auth.enroll(token_hash)
                assert str(session.user_id) == subject
                filename = tmp_path / "private" / "worker-session.json"
                session.save(filename)
                assert filename.stat().st_mode & 0o777 == 0o600
                loaded = WorkerSession.load(filename, supabase_url=origin)
                assert loaded.refresh_token == session.refresh_token
                assert session.refresh_token.get_secret_value() not in repr(session)
                with pytest.raises(RpcError, match="worker_auth_failed"):
                    await auth.enroll(token_hash)
                refreshed = await auth.refresh(loaded)
                assert refreshed.user_id == session.user_id
                refreshed.save(filename)
                assert (
                    WorkerSession.load(filename, supabase_url=origin).refresh_token
                    == refreshed.refresh_token
                )
            finally:
                deleted = await client.delete(
                    "/auth/v1/admin/users/" + subject, headers=admin_headers
                )
                assert deleted.status_code in (
                    200,
                    204,
                ), "Temporary worker identity cleanup failed"

    asyncio.run(run())
