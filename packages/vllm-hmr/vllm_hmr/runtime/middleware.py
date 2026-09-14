"""ASGI request boundary: the only point at which source is allowed to change.

Publication happens between requests, never inside one. The API process publishes
its own modules and then asks every worker to do the same, so a single request is
always served by one consistent version of the code.
"""

from __future__ import annotations

import asyncio
import json
import os

from . import telemetry
from .bootstrap import requeue_for_retry, sync_pending

DEFAULT_RPC_TIMEOUT_S = 30.0
NEVER_LOADED = "not loaded from this source root"  # the one worker rejection that is not version skew


def rpc_timeout() -> float:
    """Bounded wait for the worker fan-out, overridable via `HMR_VLLM_RPC_TIMEOUT_S`.

    vLLM's `collective_rpc` defaults to `timeout=None`, i.e. wait forever. That await sits
    inside the boundary lock, so one wedged worker would stall the boundary of every later
    request, not just this one. A non-positive or unparsable value falls back to the default
    rather than restoring the unbounded wait.
    """
    raw = os.getenv("HMR_VLLM_RPC_TIMEOUT_S")
    if not raw:
        return DEFAULT_RPC_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RPC_TIMEOUT_S
    return value if value > 0 else DEFAULT_RPC_TIMEOUT_S


def _watcher_problem(where: str, result: dict) -> dict | None:
    """The watcher complaint for one process, or `None` when its watcher is doing its job.

    Terminal state is read off the top-level `hmr_available`, not inferred from the nested
    recovery record. `sync_pending` refuses to publish once the restart budget is spent and says
    so with that field; a boundary that only looked at `watcher_recovery.exhausted` would miss the
    refusal on any later call, because a refused boundary never attempts recovery again and its
    record carries no `exhausted` key of its own.
    """
    if not result.get("watcher_failed"):
        return None
    recovery = result.get("watcher_recovery")
    exhausted = result.get("hmr_available") is False or (isinstance(recovery, dict) and recovery.get("exhausted", False))
    error = "watcher failed; source changes are no longer observed"
    if exhausted:
        # Terminal, so name it as such: the queue is not drained and no later boundary will retry.
        error += " (recovery exhausted; HMR is no longer available in this process)"
    return {"where": where, "error": error, "exhausted": exhausted}


def _version_skew_problems(worker_sync) -> list[dict]:
    """Worker rejections that signal API/worker version skew: the worker loaded the module from the managed source root but failed to reload it.

    `not loaded from this source root` is the one rejection that is not skew: that worker never imported the HMR-managed module, so it cannot be running a stale version.
    """
    if not worker_sync:
        return []
    skew: list[dict] = []
    for rank, result in enumerate(worker_sync):
        if not isinstance(result, dict):
            continue
        for item in result.get("rejected", ()):
            if not isinstance(item, dict):
                continue
            error = item.get("error", "")
            if not str(error).startswith(f"{NEVER_LOADED}:"):
                skew.append({"where": f"worker[{rank}]", "path": item.get("path"), "error": error})
    return skew


def _failures(local_sync, worker_sync) -> list[dict]:
    """Every reason this boundary did not publish everywhere it tried to, for telemetry.

    Recorded, not raised. A worker legitimately rejects a module it never imported — the
    verified CPU path loads the target in the API process only, so `not loaded from this
    source root` from a worker is the expected answer, not a fault. Failing the request on
    it would break the one topology this package has evidence for.
    """
    problems: list[dict] = []
    for where, result in (("api", local_sync), *((f"worker[{rank}]", item) for rank, item in enumerate(worker_sync or ()))):
        if not isinstance(result, dict):
            continue
        if result.get("rejected"):
            problems.append({"where": where, "rejected": result["rejected"]})
        if (problem := _watcher_problem(where, result)) is not None:
            problems.append(problem)
    return problems


class HMRBoundaryMiddleware:
    """Injected by the `vllm-hmr` CLI as `--middleware vllm_hmr.runtime.middleware.HMRBoundaryMiddleware`.

    Publishes changes at request boundaries and enforces request consistency: one request uses one version of the code.
    """

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
            # Skip the fan-out entirely if the API refused to publish (terminal failure): workers
            # publishing while the API stays on old code is the version skew the boundary prevents.
            worker_sync = None
            rpc_error = None
            # `installed` as well as `hmr_available`: both a never-installed process and a
            # terminally broken one report `hmr_available: False`, but only the second is a refusal.
            # An uninstalled API process holds no HMR-managed module at all, so it has no old
            # version for the workers to skew against — and skipping the fan-out there would also
            # silently retire the middleware/worker RPC-name contract check.
            local_refused = bool(local_sync.get("installed")) and local_sync.get("hmr_available") is False
            if engine is not None and scope.get("path", "").startswith("/v1/") and telemetry.active_scopes() == 0 and not local_refused:
                try:
                    # vLLM's own `timeout` parameter, not `asyncio.wait_for`: the executor uses it to
                    # fail the RPC internally, where `wait_for` would only abandon the await and leave
                    # the fan-out running against workers this boundary no longer waits for.
                    worker_sync = await engine.collective_rpc("vllm_hmr_sync_pending", timeout=rpc_timeout())
                except Exception as exc:
                    # Recorded, not raised: a wedged or failing worker RPC must not turn every later
                    # request into a 500. The unpublished changes stay queued for a later boundary.
                    rpc_error = f"{type(exc).__name__}: {exc}"
                    telemetry.event("worker_sync_failed", path=scope.get("path", ""), error=rpc_error)
            problems = _failures(local_sync, worker_sync)
            if rpc_error is not None:
                problems.append({"where": "workers", "error": rpc_error})
            telemetry.event("request_boundary", path=scope.get("path", ""), local_sync=local_sync, worker_sync=worker_sync, problems=problems)

            # A loaded-module rejection is worker-side evidence of a version split, regardless of
            # whether this API process had a local pending item in the same boundary. A failed RPC
            # is also unsafe on an inference boundary: it did not confirm that every worker reached
            # the same version, so fail closed rather than serving through an unknown split.
            published = local_sync.get("published") or []
            skew = _version_skew_problems(worker_sync) if scope.get("path", "").startswith("/v1/") else []
            if rpc_error is not None and scope.get("path", "").startswith("/v1/"):
                skew.append({"where": "workers", "error": rpc_error})
            if skew:
                # Re-queued before responding: `sync_pending` advanced the rescan baseline for what
                # it published here, so nothing on disk would re-announce these files and the
                # workers would stay on the old code for the lifetime of the process.
                requeued = requeue_for_retry(published)
                telemetry.event("inference_blocked", path=scope.get("path", ""), reason="worker_publication_incomplete", skew=skew, requeued=requeued)
                body = json.dumps(
                    {
                        "error": {
                            "message": "HMR published a source change in the API process that the workers did not confirm; refusing to serve this request across two versions of the same module.",
                            "type": "hmr_worker_publication_incomplete",
                            "code": None,
                            "param": None,
                        },
                        "hmr": {"skew": skew, "requeued": requeued},
                    }
                ).encode()
                await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return None

            telemetry.scope_enter()  # inside the lock: the next request must see this request as in flight

        try:
            await self.app(scope, receive, send)  # outside the lock: requests are concurrent, only their boundaries are not
        finally:
            telemetry.scope_exit()
        return None
