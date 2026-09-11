"""Opt-in vLLM endpoint plugin for HMR state and publication evidence."""

# ruff: noqa: TC002, FBT001, FBT002
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

from fastapi import FastAPI, Request

from .bootstrap import state, sync_pending


class HMRProbeEndpointPlugin:
    name = "hmr_vllm_probe"
    required_tasks = None

    def attach_router(self, app: FastAPI) -> None:
        @app.get("/__hmr__/state")
        async def hmr_state(raw_request: Request):
            engine = raw_request.app.state.hmr_probe_engine_client
            workers = [] if engine is None else await engine.collective_rpc("hmr_probe_state")
            return {"api": state(), "workers": workers}

        @app.post("/__hmr__/sync")
        async def hmr_sync(raw_request: Request, force: bool = False):
            engine = raw_request.app.state.hmr_probe_engine_client
            local = sync_pending(force=force)
            workers = []
            if engine is not None and not local.get("deferred", False):
                workers = await engine.collective_rpc("hmr_probe_sync_pending", kwargs={"force": force})
            return {"api": local, "workers": workers}

    async def init_state(self, engine_client, state_obj, _args) -> None:
        state_obj.hmr_probe_engine_client = engine_client


def create_plugin():
    return HMRProbeEndpointPlugin()
