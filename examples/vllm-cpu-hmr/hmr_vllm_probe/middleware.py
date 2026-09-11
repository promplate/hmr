"""Stable ASGI request boundary for controlled source publication."""

from __future__ import annotations

from . import telemetry
from .bootstrap import sync_pending


class HMRBoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        local_sync = {"installed": True, "skipped": "explicit sync endpoint"} if path == "/__hmr__/sync" else sync_pending()
        app = scope.get("app")
        engine = getattr(getattr(app, "state", None), "hmr_probe_engine_client", None)
        worker_sync = None
        if engine is not None and path.startswith("/v1/") and telemetry.active_scopes() == 0:
            worker_sync = await engine.collective_rpc("hmr_probe_sync_pending")
        telemetry.event("request_boundary", path=path, local_sync=local_sync, worker_sync=worker_sync)
        telemetry.scope_enter(path)

        async def send_with_marker(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                marker = telemetry.latest_marker()
                if marker:
                    headers.append((b"x-hmr-probe-code-marker", marker.encode("ascii", "strict")))
                headers.append((b"x-hmr-probe-api-pid", str(__import__("os").getpid()).encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_marker)
        finally:
            telemetry.scope_exit()
