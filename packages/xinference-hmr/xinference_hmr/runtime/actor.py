"""Actor request boundary: the only point at which source is allowed to change.

Publication happens between actor calls inside the sub-pool process that owns the
loaded model -- not in the REST process and not in the supervisor. `ModelActor` is
what receives every real inference call, so wrapping its `__on_receive__` puts the
boundary exactly where the request enters the process that will run the edited code.

Nothing about the actor or the model is replaced: the wrapper is installed on the
`ModelActor` *class* before any instance exists, and it delegates to the original
bound coroutine. The loaded model object, its class, its parameters and the actor
instance are all untouched.
"""

from __future__ import annotations

import os
from typing import Any

from . import telemetry
from .bootstrap import sync_pending

_PATCHED = False

# Actor methods that are not a request: publishing before them would let a health poll
# or a metrics scrape swap source, and `get_pid`/`state` are exactly what the smoke
# calls to *observe* a publication, so they must not cause one.
NON_REQUEST_METHODS = frozenset(
    {
        "get_pid",
        "record_metrics",
        "get_pending_requests_count",
        "decrease_serve_count",
        "wait_for_load",
        "need_create_pools",
        "get_pool_addresses",
        "set_pool_addresses",
        "set_worker_addresses",
        "get_driver_info",
        "model_uid",
        "xinference_hmr_state",
        "xinference_hmr_sync",
    }
)


def install() -> bool:
    """Wrap `ModelActor.__on_receive__` so every actor call is a publication boundary.

    Returns whether the patch was applied. Called from the sub-pool process at `site`
    time, i.e. before `xinference.core.model` is imported, so the import happens here
    rather than at module scope.
    """
    global _PATCHED
    if _PATCHED:
        return False

    from xinference.core.model import ModelActor

    original = ModelActor.__on_receive__

    async def __on_receive__(self, message):  # noqa: N807 - this *is* the dunder being replaced
        # `message` is `(method_name, call_method, args, kwargs)`; see xoscar's `_BaseActor`.
        method = message[0] if isinstance(message, tuple) and message else None
        if not isinstance(method, str) or method in NON_REQUEST_METHODS or method.startswith("_"):
            return await original(self, message)

        # No lock around this: the actor's own `self._lock` already serialises `__on_receive__`
        # for every method without a no-lock hint, and `sync_pending` defers while
        # `active_scopes()` is non-zero, which covers the streaming methods that return a
        # generator and leave the lock before the response is finished.
        sync = sync_pending()
        telemetry.event("actor_boundary", method=method, pid=os.getpid(), sync=sync)
        telemetry.scope_enter()
        try:
            return await original(self, message)
        finally:
            telemetry.scope_exit()

    ModelActor.__on_receive__ = __on_receive__
    _PATCHED = True
    telemetry.event("actor_patched", pid=os.getpid())
    return True


def install_debug_endpoints() -> None:
    """Expose HMR state on `ModelActor` itself, so evidence comes from the model's own process.

    A REST-side report could only ever describe the API process. These two methods answer
    from inside the actor, which is the process this package claims to reload.
    """
    from xinference.core.model import ModelActor

    if hasattr(ModelActor, "xinference_hmr_state"):
        return

    def xinference_hmr_state(self) -> dict[str, Any]:  # noqa: ARG001 - actor methods take `self`
        from .bootstrap import state

        return state()

    def xinference_hmr_sync(self) -> dict[str, Any]:  # noqa: ARG001
        return sync_pending(force=True)

    ModelActor.xinference_hmr_state = xinference_hmr_state
    ModelActor.xinference_hmr_sync = xinference_hmr_sync
