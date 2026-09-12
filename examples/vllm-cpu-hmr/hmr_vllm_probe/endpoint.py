"""A read-only HTTP window onto the packaged runtime's state, for the CPU smoke.

`vllm-hmr` ships no debug endpoint: its state lives in the API process and in each
worker, reachable in-process or over `collective_rpc` only. This plugin exposes that
state unchanged. It installs no watcher, publishes nothing, and adds no HMR
behaviour of its own, so what the smoke asserts on is the packaged runtime.
"""

# ruff: noqa: TC002
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

from fastapi import FastAPI, Request
from vllm_hmr.runtime.bootstrap import state


class HMRProbeEndpointPlugin:
    name = "hmr_vllm_probe"
    required_tasks = None

    def attach_router(self, app: FastAPI) -> None:
        @app.get("/__hmr__/state")
        async def hmr_state(raw_request: Request):
            engine = raw_request.app.state.hmr_probe_engine_client
            workers = [] if engine is None else await engine.collective_rpc("hmr_probe_identity")
            return {"api": state(), "workers": workers}

    async def init_state(self, engine_client, state_obj, _args) -> None:
        state_obj.hmr_probe_engine_client = engine_client


def create_plugin():
    return HMRProbeEndpointPlugin()
