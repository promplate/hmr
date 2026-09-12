"""ASGI request boundary: the only point at which source is allowed to change.

Publication happens between requests, never inside one. The API process publishes
its own modules and then asks every worker to do the same, so a single request is
always served by one consistent version of the code.
"""

from __future__ import annotations

import asyncio

from . import telemetry
from .bootstrap import sync_pending


class HMRBoundaryMiddleware:
    """Injected by the `vllm-hmr` CLI as `--middleware vllm_hmr.runtime.middleware.HMRBoundaryMiddleware`."""

    def __init__(self, app):
        self.app = app
        # Serialises the boundary only, never the request. Both the deferral rule
        # (`sync_pending` returns early while a scope is active) and the worker-RPC
        # rule (`active_scopes() == 0`) are check-then-act on a counter that any other
        # request can raise, and `await collective_rpc(...)` is a suspension point
        # sitting between the check and `scope_enter`. Without this, two requests
        # arriving together both read zero, both publish, and both fan out to the
        # workers, which is the one thing the boundary exists to prevent.
        self._boundary = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async with self._boundary:
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
            telemetry.scope_enter()  # inside the lock: the next request must see this request as in flight

        try:
            await self.app(scope, receive, send)  # outside the lock: requests are concurrent, only their boundaries are not
        finally:
            telemetry.scope_exit()
        return None
