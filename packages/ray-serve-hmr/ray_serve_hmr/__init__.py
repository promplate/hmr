"""Python-source hot module replacement for a Ray Serve replica's request path.

Call `install()` from your deployment's `__init__`, inside the replica actor. From then on,
editing `ray/serve/_private/replica.py` in the tree this process imported takes effect on the
next request handled by that same replica actor -- same PID, same deployment instance, same
model object, no reconfigure, no replica replacement, no restart.

    from ray import serve
    import ray_serve_hmr

    @serve.deployment(num_replicas=1)
    class MyModel:
        def __init__(self):
            self.model = load_model()   # untouched by HMR, ever
            ray_serve_hmr.install()

What this is not: a deployment update, a `reconfigure`, an autoscaling event, or a replica
restart. Those all replace the object that holds the model. This replaces module code around a
model object that stays the same object.

Scope: exactly one file (`ray_serve_hmr.runtime.scope`), one replica, verified on CPU. See
README.md for what the evidence does and does not cover.
"""

from __future__ import annotations

from .runtime import install, publish_now, state

__all__ = ["install", "publish_now", "state"]
