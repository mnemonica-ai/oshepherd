import asyncio
import json
import uuid

import httpx

from oshepherd.backends.supabase.client import RpcClient
from oshepherd.backends.supabase.storage import WorkerResultStorage


def test_upload_lost_ack_reads_same_object_and_never_replaces_it():
    async def run():
        stored = None
        requests = []
        content = b'{"done":true}'

        async def handler(request):
            nonlocal stored
            requests.append((request.method, str(request.url)))
            assert request.headers["apikey"] == "publishable-key"
            assert request.headers["Authorization"] == "Bearer worker-jwt"
            if request.method == "GET":
                return (
                    httpx.Response(400, json={"statusCode": "404"})
                    if stored is None
                    else httpx.Response(200, content=stored)
                )
            assert request.headers["x-upsert"] == "false"
            stored = request.content
            raise httpx.RemoteProtocolError("upload committed; acknowledgement lost")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rpc = RpcClient(
                "https://project.test",
                "publishable-key",
                access_token="worker-jwt",
                schema="oshepherd_spike_api",
                client=client,
            )
            storage = WorkerResultStorage(rpc)
            claim = {
                "job_id": str(uuid.uuid4()),
                "attempt_id": str(uuid.uuid4()),
                "max_output_bytes": 1024,
            }
            descriptor = await storage.upload(str(uuid.uuid4()), claim, content)
            assert descriptor["inline"] is None and descriptor["size_bytes"] == len(
                content
            )
            assert stored == content
            assert [method for method, _ in requests] == ["GET", "POST", "GET"]
            assert len({url for _, url in requests}) == 1

    asyncio.run(run())
