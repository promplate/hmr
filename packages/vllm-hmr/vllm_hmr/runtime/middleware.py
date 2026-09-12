"""ASGI request boundary: the only point at which source is allowed to change.

Publication happens between requests, never inside one. The API process publishes
its own modules and then asks every worker to do the same, so a single request is
always served by one consistent version of the code.
"""

from __future__ import annotations

from . import telemetry
from .bootstrap import sync_pending


class HMRBoundaryMiddleware:
    """Injected by the `vllm-hmr` CLI as `--middleware vllm_hmr.runtime.middleware.HMRBoundaryMiddleware`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        local_sync = sync_pending()
        app = scope.get("app")
        engine = getattr(getattr(app, "state", None), "engine_client", None)
        # Workers keep their own module registries and their own watchers, so the API
        # process publishing alone would leave them on the old code. Inference paths
        # only: health and metrics must not pay a collective RPC per request.
        if engine is not None and scope.get("path", "").startswith("/v1/") and telemetry.active_scopes() == 0:
            worker_sync = await engine.collective_rpc("vllm_hmr_sync_pending")
        else:
            worker_sync = None
        telemetry.event("request_boundary", path=scope.get("path", ""), local_sync=local_sync, worker_sync=worker_sync)

        telemetry.scope_enter()
        try:
            await self.app(scope, receive, send)
        finally:
            telemetry.scope_exit()
        return None
