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
from .bootstrap import NON_RETRYABLE_REJECTION_PREFIXES, requeue_for_retry, sync_pending

DEFAULT_RPC_TIMEOUT_S = 30.0


def rpc_timeout() -> float:
    """Bounded wait for the worker fan-out, overridable via `HMR_VLLM_RPC_TIMEOUT_S`.

    vLLM 0.28's UniProcExecutor accepts its own timeout but does not enforce it, so the API
    boundary owns this deadline. A non-positive or unparsable value falls back to the default
    rather than restoring an unbounded wait.
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
            if not any(str(error).startswith(prefix) for prefix in NON_RETRYABLE_REJECTION_PREFIXES):
                skew.append({"where": f"worker[{rank}]", "path": item.get("path"), "error": error})
    return skew


async def _send_503(send, message: str, error_type: str, **hmr) -> None:
    """Refuse this request in the OpenAI error shape, with the HMR detail under its own key."""
    body = json.dumps({"error": {"message": message, "type": error_type, "code": None, "param": None}, "hmr": hmr}).encode()
    await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


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
        # Pending worker RPC task and its published records: preserved across deadline so
        # later requests can confirm it completed or stay fail-closed while it runs.
        self._pending_rpc: asyncio.Task | None = None
        self._pending_published: list[dict] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Health and metrics must not enter the boundary lock at all: a wedged inference
        # fan-out inside the lock would otherwise block them too. This passthrough happens
        # before the lock, so these paths never wait on a stalled RPC.
        if not scope.get("path", "").startswith("/v1/"):
            return await self.app(scope, receive, send)

        async with self._boundary:
            worker_sync = None
            rpc_error = None
            # A fan-out an earlier boundary started but stopped waiting for. Its outcome decides
            # this request: until it is known, the workers may be on either version, and starting
            # a second fan-out would race two publications against the same workers.
            if (task := self._pending_rpc) is not None:
                if not task.done():
                    telemetry.event("inference_blocked", path=scope.get("path", ""), reason="worker_rpc_still_running")
                    await _send_503(
                        send,
                        "A previous HMR worker publication is still running; refusing to serve this request until it is confirmed.",
                        "hmr_worker_rpc_incomplete",
                        reason="worker_rpc_still_running",
                    )
                    return None
                # Finished. Its records are this boundary's to settle, and `sync_pending` is not
                # called until it is settled: publishing more here would put the API further ahead
                # of workers whose state this boundary has not confirmed yet.
                self._pending_rpc = None
                owed_published, self._pending_published = self._pending_published, []
                try:
                    worker_sync = task.result()
                except asyncio.CancelledError:
                    rpc_error = "CancelledError: worker RPC task was cancelled"
                    telemetry.event("worker_sync_failed", path=scope.get("path", ""), error=rpc_error, deferred_from="deadline")
                except Exception as exc:
                    rpc_error = f"{type(exc).__name__}: {exc}"
                    telemetry.event("worker_sync_failed", path=scope.get("path", ""), error=rpc_error, deferred_from="deadline")
                skew = _version_skew_problems(worker_sync)
                if rpc_error is not None:
                    skew.append({"where": "workers", "error": rpc_error})
                if skew:
                    requeued = requeue_for_retry(owed_published)
                    telemetry.event("inference_blocked", path=scope.get("path", ""), reason="deferred_worker_publication_failed", skew=skew, requeued=requeued)
                    await _send_503(
                        send,
                        "The HMR worker publication this API process was waiting on failed; refusing to serve this request across two versions of the same module.",
                        "hmr_worker_publication_incomplete",
                        skew=skew,
                        requeued=requeued,
                    )
                    return None
                telemetry.event("deferred_worker_publication_confirmed", path=scope.get("path", ""), worker_sync=worker_sync, published=owed_published)

            local_sync = sync_pending()
            app = scope.get("app")
            engine = getattr(getattr(app, "state", None), "engine_client", None)
            # Workers keep their own module registries and their own watchers, so the API
            # process publishing alone would leave them on the old code. Only inference paths
            # fan out; non-/v1/ was already passed through above before entering this lock.
            # Skip the fan-out entirely if the API refused to publish (terminal failure): workers
            # publishing while the API stays on old code is the version skew the boundary prevents.
            # `installed` as well as `hmr_available`: both a never-installed process and a
            # terminally broken one report `hmr_available: False`, but only the second is a refusal.
            # An uninstalled API process holds no HMR-managed module at all, so it has no old
            # version for the workers to skew against — and skipping the fan-out there would also
            # silently retire the middleware/worker RPC-name contract check.
            local_refused = bool(local_sync.get("installed")) and local_sync.get("hmr_available") is False
            owed_published = list(local_sync.get("published") or [])
            if engine is not None and telemetry.active_scopes() == 0 and not local_refused:
                # A task plus `shield`, not vLLM's own `timeout`: 0.28's `UniProcExecutor.collective_rpc`
                # accepts that parameter and never reads it, so the deadline has to live here. Shielded
                # because a bare `wait_for` would cancel a fan-out that is already mid-publication and
                # leave the workers in a state nobody observed; keeping the task is what lets the next
                # boundary read the real outcome instead of assuming one.
                rpc_task = asyncio.create_task(engine.collective_rpc("vllm_hmr_sync_pending", timeout=None))
                try:
                    worker_sync = await asyncio.wait_for(asyncio.shield(rpc_task), rpc_timeout())
                except TimeoutError:
                    # Deliberately not cancelled: the deadline is the API's, not the fan-out's.
                    self._pending_rpc = rpc_task
                    self._pending_published = owed_published
                    telemetry.event("worker_rpc_deadline_exceeded", path=scope.get("path", ""), timeout=rpc_timeout())
                    await _send_503(
                        send,
                        "HMR worker publication exceeded its deadline; refusing to serve this request until the workers confirm.",
                        "hmr_worker_rpc_incomplete",
                        reason="worker_rpc_deadline_exceeded",
                    )
                    return None
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        # The client went away mid-fan-out. The fan-out itself did not, so it is
                        # handed to the next boundary exactly as the deadline hands it over.
                        self._pending_rpc = rpc_task
                        self._pending_published = owed_published
                        raise
                    # The RPC task itself was cancelled; this request is still alive, so handle it
                    # as a failed fan-out below rather than impersonating a client disconnect.
                    rpc_error = "CancelledError: worker RPC task was cancelled"
                    telemetry.event("worker_sync_failed", path=scope.get("path", ""), error=rpc_error)
                    worker_sync = None
                except Exception as exc:
                    # Recorded, not raised: a failing worker RPC must not turn every later request
                    # into a 500. It still blocks this request below, via `skew`.
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
            skew = _version_skew_problems(worker_sync)
            if rpc_error is not None:
                skew.append({"where": "workers", "error": rpc_error})
            if skew:
                # Re-queued before responding: `sync_pending` advanced the rescan baseline for what
                # it published here, so nothing on disk would re-announce these files and the
                # workers would stay on the old code for the lifetime of the process.
                requeued = requeue_for_retry(owed_published)
                telemetry.event("inference_blocked", path=scope.get("path", ""), reason="worker_publication_incomplete", skew=skew, requeued=requeued)
                await _send_503(
                    send,
                    "HMR published a source change in the API process that the workers did not confirm; refusing to serve this request across two versions of the same module.",
                    "hmr_worker_publication_incomplete",
                    skew=skew,
                    requeued=requeued,
                )
                return None

            telemetry.scope_enter()  # inside the lock: the next request must see this request as in flight

        try:
            await self.app(scope, receive, send)  # outside the lock: requests are concurrent, only their boundaries are not
        finally:
            telemetry.scope_exit()
        return None
