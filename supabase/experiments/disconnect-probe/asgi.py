"""Temporary ASGI event tracing; never logs headers or request/response content."""

import asyncio
import json
import time
from uuid import UUID

import anyio


class TraceRequests:
    def __init__(self, app, reporter=None):
        self.app = app
        self.reporter = reporter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            marker = str(UUID(headers.get(b"x-oshepherd-probe", b"").decode("ascii")))
        except (ValueError, UnicodeError):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        route = path if path in ("/api/generate", "/api/chat") else "other"
        start = time.monotonic()
        events = []

        def event(kind, **fields):
            if len(events) < 32:
                events.append(
                    {
                        "event": kind,
                        "seconds": round(time.monotonic() - start, 3),
                        **fields,
                    }
                )

        async def traced_receive():
            try:
                message = await receive()
            except asyncio.CancelledError:
                event("receive-task-cancelled")
                raise
            event("receive", type=message.get("type"))
            return message

        async def traced_send(message):
            try:
                await send(message)
            except BaseException as error:
                event("send-error", type=type(error).__name__)
                raise
            event(
                "send",
                type=message.get("type"),
                bytes=len(message.get("body", b"")),
                more_body=message.get("more_body", False),
            )

        try:
            await self.app(scope, traced_receive, traced_send)
        finally:
            # These static routes suffice; never log IDs, query strings or URLs.
            trace = {
                "probe": marker,
                "route": route,
                "asgi": scope.get("asgi"),
                "events": events,
            }
            if self.reporter is not None:
                # Some provider log streams omit logs from aborted requests.
                # An owned private report is independent of log delivery.
                with anyio.move_on_after(5, shield=True):
                    try:
                        await self.reporter(trace)
                    except Exception:
                        event("report-write-failed")
            print(
                "oshepherd_disconnect_trace " + json.dumps(trace),
                flush=True,
            )
