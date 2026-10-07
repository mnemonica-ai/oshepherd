"""Bounded operator cleanup: SQL leases intents; Storage removes actual bytes."""

import asyncio
import logging
import signal
import time
from uuid import UUID

import httpx

from .client import RpcClient, RpcError, bounded_response

logger = logging.getLogger(__name__)


class ResultMaintenance:
    def __init__(self, rpc, cluster_id, *, batch=32):
        self.rpc = rpc
        self.cluster = str(UUID(str(cluster_id)))
        if type(batch) is not int or not 1 <= batch <= 64:
            raise ValueError("Invalid maintenance batch")
        self.batch = batch
        self.bucket = (
            "oshepherd-spike-results"
            if rpc.schema == "oshepherd_spike_api"
            else "oshepherd-results"
        )

    def validate_paths(self, paths):
        if not isinstance(paths, list) or len(paths) > self.batch:
            raise RpcError("protocol_mismatch")
        for path in paths:
            if not isinstance(path, str):
                raise RpcError("protocol_mismatch")
            parts = path.split("/")
            try:
                valid = (
                    len(parts) == 4
                    and parts[-1] == "result.json"
                    and parts[0] == self.cluster
                    and all(str(UUID(part)) == part for part in parts[:3])
                )
            except ValueError:
                valid = False
            if not valid:
                raise RpcError("invalid_cleanup_path")
        if len(paths) != len(set(paths)):
            raise RpcError("protocol_mismatch")

    async def delete_objects(self, paths):
        # A lost HTTP acknowledgement does not erase intent. SQL subsequently
        # verifies absence before allowing an acknowledgement to forget a path.
        try:
            async with asyncio.timeout(10):
                async with self.rpc.client.stream(
                    "DELETE",
                    self.rpc.url + "/storage/v1/object/" + self.bucket,
                    headers=self.rpc.headers(),
                    json={"prefixes": paths},
                ) as response:
                    await bounded_response(response, 65536)
                    return response.is_success or response.status_code == 404
        except (httpx.TransportError, TimeoutError, RpcError):
            return False

    async def cycle(self):
        tick = await self.rpc.rpc(
            "maintenance_tick", {"cluster_id": self.cluster, "limit": self.batch}
        )
        try:
            claim = str(UUID(tick["claim_id"]))
            paths = tick["object_paths"]
        except (KeyError, ValueError, TypeError):
            raise RpcError("protocol_mismatch") from None
        self.validate_paths(paths)
        counts = {}
        for name in ("reaped", "purged", "scanned", "enqueued"):
            count = tick.get(name)
            if type(count) is not int or not 0 <= count <= self.batch:
                raise RpcError("protocol_mismatch")
            counts[name] = count
        counts.update(deleted=0, retrying=0)
        if paths:
            removed = await self.delete_objects(paths)
            ack = await self.rpc.rpc(
                "complete_maintenance",
                {
                    "cluster_id": self.cluster,
                    "claim_id": claim,
                    "deleted_paths": paths if removed else [],
                    "failed_paths": [] if removed else paths,
                },
            )
            for name in ("deleted", "retrying"):
                count = ack.get(name)
                if type(count) is not int or not 0 <= count <= len(paths):
                    raise RpcError("protocol_mismatch")
                counts[name] = count
        return counts

    async def run(self, stop, *, interval=10):
        if not 10 <= interval <= 3600:
            raise ValueError("Invalid maintenance interval")
        while not stop.is_set():
            started = time.monotonic()
            try:
                async with asyncio.timeout(30):
                    counts = await self.cycle()
                # Only aggregate categories; never object paths, payloads,
                # provider responses or credentials in routine diagnostics.
                logger.info(
                    "maintenance reaped=%d purged=%d scanned=%d enqueued=%d deleted=%d retrying=%d",
                    *(
                        counts[name]
                        for name in (
                            "reaped",
                            "purged",
                            "scanned",
                            "enqueued",
                            "deleted",
                            "retrying",
                        )
                    ),
                )
            except RpcError as error:
                logger.warning("maintenance error=%s", error.code)
            except TimeoutError:
                logger.warning("maintenance error=cycle_timeout")
            try:
                await asyncio.wait_for(
                    stop.wait(), max(0.05, interval - (time.monotonic() - started))
                )
            except TimeoutError:
                pass


async def run_maintenance(config, *, once=False):
    rpc = RpcClient(
        config.SUPABASE_URL,
        config.SUPABASE_SECRET_KEY.get_secret_value(),
        schema=config.SUPABASE_SCHEMA,
    )
    runner = ResultMaintenance(
        rpc, config.OSHEPHERD_CLUSTER_ID, batch=config.MAINTENANCE_BATCH
    )
    signals = []
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    try:
        if once:
            async with asyncio.timeout(30):
                return await runner.cycle()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
            signals.append(signum)
        await runner.run(stop, interval=config.MAINTENANCE_SECONDS)
    finally:
        for signum in signals:
            loop.remove_signal_handler(signum)
        await rpc.close()
