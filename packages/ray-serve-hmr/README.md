# ray-serve-hmr

Python-source hot module replacement for a Ray Serve replica's request path.

Editing `ray/serve/_private/replica.py` takes effect on the next request handled by the same
replica actor: same PID, same deployment instance, same model object, no reconfigure, no replica
replacement, no restart.

```python
from ray import serve
import ray_serve_hmr

@serve.deployment(num_replicas=1)
class MyModel:
    def __init__(self):
        self.model = load_model()   # never touched by HMR
        ray_serve_hmr.install()
```

## How it works, and why it has to

Three facts about Ray Serve decide the mechanism.

**The import-hook route is unavailable.** `reactivity`'s `ReactiveModuleFinder` puts every
site-packages directory into `excludes` unconditionally, and its `find_spec` returns early for
anything already in `sys.modules`. `ray.serve._private.replica` is both — it defines the actor
class, so it is imported before any deployment code runs. So the module object is converted in
place with `patch_module` instead.

**`patch_module` alone is not enough.** `ReactiveModule.__init__` copies the pre-existing
namespace onto the module object's own `__dict__`, and `__getattribute__` finds those copies
before `__getattr__` — the only route into the reactive namespace, and therefore to reloaded
code. Without dropping those copies, `module.Replica` keeps returning the pre-reload class
forever while the reload silently succeeds. This package drops them.

**The live replica holds references a module reload does not follow.** Each one would keep
serving old code:

| Reference | Why a reload misses it |
| --- | --- |
| `ReplicaActor._replica_impl` | a `Replica` instance built from the old class object |
| `Replica._user_callable_wrapper` | a `UserCallableWrapper` instance, likewise |
| `UserCallableWrapper._cached_user_method_info` | caches `UserMethodInfo` holding the **bound method** of the user's instance; requests call `.callable` directly |

Publication therefore reloads the module, rebinds `__class__` on the two live instances (which
preserves their identity and their `__dict__`, i.e. the model), and clears the method-info cache
so the next request re-derives the bound method from the still-live user object.

The actor class itself is out of reach and is not a target: the controller builds it as
`type(name, (ReplicaActor,), dict(ReplicaActor.__dict__))` and ships it over cloudpickle.

## Safety

Publication runs on the replica's **main event loop** — the loop that dispatches
`handle_request*` — so it cannot interleave with request dispatch. It additionally waits for
zero ongoing requests, so an in-flight request finishes on the old implementation rather than
spanning the swap. The watcher thread only enqueues.

A syntax error is caught before the loader sees it (`ReactiveModule.__load` routes `SyntaxError`
to `sys.excepthook` and leaves the module half-updated with no way to tell). A runtime error
during re-execution is caught and the live instances are left bound to the old classes. Either
way the replica keeps serving and the change stays pending for a fixed file.

## Scope

Watches exactly one file: `ray/serve/_private/replica.py`, the module that owns the replica-side
request path. The source root defaults to the tree the live process imported, and an explicit
root must resolve to that same file — nothing is ever copied, so the file edited is the file the
interpreter loaded.

Widening the set is not a config change: it needs evidence that the new target survives in-place
replacement on a live replica.

## What the evidence covers

Verified: Ray 2.58.0 on the official `rayproject/ray:2.58.0-py312-cpu` image, CPU, a single
fixed replica, one HTTP request path, a real `sshleifer/tiny-gpt2` model behind an
OpenAI-compatible API. See `examples/ray-serve-cpu-hmr`.

The core package is installed from one pinned `promplate/pyth-on-line` revision's source
tarball, never from an index: the receipt records the dist's PEP 610 `direct_url.json` and the
SHA-256 of the installed `reactivity/hmr/core.py`, checked against a value computed on the host
from that revision independently of the image.

Not established: GPU, multiple replicas or atomic publication across them, the proxy or
controller processes, gRPC and direct-ingress paths, streaming responses mid-stream, model weight
or deployment-config hot update, and any file other than the one in scope.

`py312` rather than `py311`: `hmr` requires Python >= 3.12 in every published version, so the
official py311 image has no installable candidate at all.
