"""In-place hot replacement of `ray.serve._private.replica`, and the rebinds it forces.

Reloading the module is the easy half. The hard half is that a live replica holds references
that a module reload does not follow, and every one of them would keep serving old code:

- `ReplicaActor._replica_impl` is a `Replica` instance built from the OLD class object.
- `Replica._user_callable_wrapper` is a `UserCallableWrapper` instance, likewise.
- `UserCallableWrapper._cached_user_method_info` caches `UserMethodInfo` objects that hold the
  BOUND method of the user's deployment instance. Requests call `user_method_info.callable`
  directly, so a stale entry bypasses everything else.

So publication is: reload the module, then rebind `__class__` on the two live instances (which
preserves their identity and their `__dict__`, i.e. the model), then drop the method-info cache
so the next request re-derives the bound method from the still-live user instance.

The user's deployment object itself is never touched: it is not defined in the watched module,
it holds the model, and re-creating it is exactly the "replica replacement" this is not.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .scope import TARGET_MODULE

if TYPE_CHECKING:
    from types import ModuleType

_PATCH_LOCK = threading.Lock()
_PATCHED: ModuleType | None = None


def _load_handle(module: ModuleType):
    """The loader is private on purpose; `uvicorn-hmr` reaches it by the same mangled route.

    Read the descriptor off the class: on an instance the mangled name resolves against the
    module's own namespace dict instead of the descriptor.
    """
    from reactivity.hmr.core import ReactiveModule

    descriptor = ReactiveModule.__load if TYPE_CHECKING else ReactiveModule._ReactiveModule__load  # noqa: SLF001
    return descriptor.__get__(module, ReactiveModule)


def patch_target_module() -> ModuleType:
    """Convert the already-imported target into a `ReactiveModule`, exactly once.

    `patch_meta_path` cannot do this: `ReactiveModuleFinder` puts every site-packages directory
    into `excludes` unconditionally, and `find_spec` returns early for anything already in
    `sys.modules` — and `ray.serve._private.replica` is both.
    """
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED is not None:
            return _PATCHED
        from reactivity.hmr.core import patch_module

        module = patch_module(TARGET_MODULE)

        # `ReactiveModule.__init__` does `self.__dict__.update(namespace)`, which copies every
        # existing name onto the module object itself. `__getattribute__` finds those copies
        # first, so `__getattr__` -- the only route into the reactive namespace, and therefore
        # the only route to reloaded code -- is never reached, and `module.Replica` keeps
        # returning the pre-reload class forever. Drop the user-level copies. The
        # `_ReactiveModule__*` entries and dunders are the module's own machinery: deleting
        # those sends `__getattr__` into infinite recursion.
        real_dict = object.__getattribute__(module, "__dict__")
        for name in [key for key in list(real_dict) if not key.startswith("_ReactiveModule__") and not (key.startswith("__") and key.endswith("__"))]:
            del real_dict[name]

        _PATCHED = module
        return module


def reload_target(module: ModuleType) -> None:
    """Re-execute the watched file into the live module namespace.

    `sys.excepthook` swallows a `SyntaxError` inside `ReactiveModule.__load`, so callers must
    do their own syntax preflight; a runtime error during exec propagates out of here.
    """
    load = _load_handle(module)
    load.invalidate()
    load()


def live_actor() -> Any:
    """The `ReplicaActor` instance this worker process is running, via Ray's own registry.

    Ray keeps the actor instance in `global_worker.actors`, which is the same object the actor
    task dispatcher calls into. A `gc.get_objects()` scan would also find it, but that walks
    ~160k objects and can pick up a stale instance from an earlier deployment version.
    """
    import ray._private.worker

    actors = list(ray._private.worker.global_worker.actors.values())  # noqa: SLF001
    if len(actors) != 1:
        raise RuntimeError(f"expected exactly one actor in this worker, found {len(actors)}: {[type(actor).__name__ for actor in actors]}")
    return actors[0]


def rebind_live_instances(module: ModuleType) -> dict[str, Any]:
    """Point the live replica objects at the reloaded classes and drop the stale method cache.

    Returns what was rebound, including the pre/post identities the smoke asserts on.
    """
    actor = live_actor()
    replica_impl = actor._replica_impl  # noqa: SLF001
    wrapper = replica_impl._user_callable_wrapper  # noqa: SLF001

    new_replica_cls = module.Replica
    new_wrapper_cls = module.UserCallableWrapper

    result: dict[str, Any] = {
        "actor_type": type(actor).__name__,
        "replica_id_before": id(replica_impl),
        "wrapper_id_before": id(wrapper),
        "user_callable_id_before": id(wrapper._callable),  # noqa: SLF001
        "replica_class_changed": type(replica_impl) is not new_replica_cls,
        "wrapper_class_changed": type(wrapper) is not new_wrapper_cls,
        "cached_methods_dropped": sorted(wrapper._cached_user_method_info),  # noqa: SLF001
    }

    replica_impl.__class__ = new_replica_cls
    wrapper.__class__ = new_wrapper_cls
    # Each entry holds a bound method of the user instance captured at first call. Clearing is
    # enough -- `get_user_method_info` re-derives from `self._callable`, which is untouched, so
    # the user's model-holding object stays the same object and only the code around it moves.
    wrapper._cached_user_method_info.clear()  # noqa: SLF001

    result["replica_id_after"] = id(replica_impl)
    result["wrapper_id_after"] = id(wrapper)
    result["user_callable_id_after"] = id(wrapper._callable)  # noqa: SLF001
    result["replica_class_is_new"] = type(replica_impl) is new_replica_cls
    result["wrapper_class_is_new"] = type(wrapper) is new_wrapper_cls
    return result


def module_origin(module: ModuleType) -> Path | None:
    """The live module's own `__file__`: the only proof of where its code came from.

    `__file__` is in the loader's `STATIC_ATTRS`, so this reads the real namespace entry and
    records no reactive dependency.
    """
    file = getattr(module, "__file__", None)
    return None if file is None else Path(file).resolve()
