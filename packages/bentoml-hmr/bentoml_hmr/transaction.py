"""Namespace, dirty-flag, and proxy rollback for a failed reload; in-place side effects are not undone."""

from typing import Any, NamedTuple


def _load_handle(module):
    """Access the intentionally private loader from a core-owned frame."""
    from reactivity.hmr import core

    helper = getattr(core, "_hmr_probe_load", None)
    if helper is None:
        namespace = core.__dict__
        exec("def _hmr_probe_load(module):\n    return module.load\n", namespace)
        helper = namespace["_hmr_probe_load"]
    return helper(module)


class _ModuleSnapshot(NamedTuple):
    """Everything rollback must put back, captured before the first invalidation of a transaction.

    The namespace is a shallow copy: rollback restores bindings (which name refers to which object),
    but not in-place mutations to shared mutable objects. A failed load that appends to a list or
    mutates a dict that was already bound leaves those mutations behind — rollback only puts the
    name back to the original object reference, it does not rewind what that object contains.
    """

    namespace: dict[str, Any]
    dirty: bool
    doc: Any
    flags: Any


def _snapshot_module_state(module) -> _ModuleSnapshot:
    """Capture pre-transaction state for rollback: namespace shallow copy, `load.dirty`, and the private `__doc__`/`__flags`.

    `module.__dict__` is the reactive namespace, while `__doc__` and the name-mangled `__flags` live in
    the real module dict reached through `object.__getattribute__` — two different mappings, both of
    which `ReactiveModule.__load` writes on a successful reload.
    """
    real_dict = object.__getattribute__(module, "__dict__")
    load = _load_handle(module)
    return _ModuleSnapshot(dict(module.__dict__), bool(load.dirty), real_dict.get("__doc__"), real_dict.get("_ReactiveModule__flags"))


def _restore_module_state(module, snapshot: _ModuleSnapshot) -> None:
    """Put a module back the way `snapshot` found it, in place.

    The namespace dict is mutated in place rather than replaced: `ReactiveModule` holds it as
    `__namespace`, the proxy holds the same object as its `_data`, and every function `exec` defined
    in an earlier load carries it as `__globals__`. Rebinding it would leave all three pointing at a
    dict the module no longer uses. Writes go through the namespace proxy, not the raw dict, because
    the proxy tracks per-name `Signal`s and a raw `del` leaves a signal claiming a name that is gone
    (verified: the proxy then raises `KeyError` for a name still in `_keys`).

    `_ReactiveModule__load` is skipped: it is the load handle this very call is invoked from, it is
    stable across reloads, and routing it through the proxy would make the loader a reactive name.
    `__spec__`, `__loader__`, `__name__` and `__file__` need no special casing — they are in the
    snapshot, unchanged by `exec`, so the identity comparison below leaves them untouched.
    """
    namespace = module.__dict__
    real_dict = object.__getattribute__(module, "__dict__")
    # Deleting a namespace name has no public path: `ReactiveModule` defines no `__delattr__`, so
    # `delattr` reaches `ModuleType.__delattr__`, which looks in the real dict and raises
    # `AttributeError` for a name that only exists in the namespace (verified). `hmr`'s own
    # `utils.py` reads this same attribute the same way in `cache_across_reloads`.
    proxy = module._ReactiveModule__namespace_proxy  # noqa: SLF001
    for key in [key for key in namespace if key not in snapshot.namespace and key != "_ReactiveModule__load"]:
        # A name the failed load bound that did not exist before it. Which mapping learned about it
        # decides how it comes back out. `__load` runs `exec(code, namespace, proxy)`: module-level
        # statements compile to `STORE_NAME` and write the proxy, keeping the per-name signal in
        # step, but a `global` write inside a function compiles to `STORE_GLOBAL` and writes the raw
        # namespace dict directly, which the proxy never observes.
        signal = proxy._keys.get(key)  # noqa: SLF001 - `.get`, not `[]`: `_keys` is a defaultdict that would mint a signal for a name that has none
        if signal is not None and signal._value:  # noqa: SLF001
            del proxy[key]  # the proxy published this name, so its signal and `_iter` must both learn it is gone
        else:
            # The proxy never published it, so there is nothing reactive to retract: its signal
            # already reads absent and `__iter__` already skips it, which is exactly why
            # `ReactiveMappingProxy.__delitem__` raises `KeyError` for it (verified — this was
            # `KeyError` escaping `sync_pending` as a 500). Dropping it from the raw dict restores
            # the namespace without inventing a signal transition no subscriber ever saw.
            del namespace[key]
    for key, value in snapshot.namespace.items():
        if key == "_ReactiveModule__load":
            continue
        signal = proxy._keys.get(key)  # noqa: SLF001 - inspect internal signal state to detect desync
        # Restore if: key missing, identity changed, OR signal is desynced (False/None while key exists).
        # Signal desync can occur if a partial failed load flipped a signal without removing the key.
        if key not in namespace or namespace[key] is not value or (signal is not None and not signal._value):  # noqa: SLF001
            # `setattr`, not a raw dict write: `ReactiveModule.__setattr__` routes an external
            # caller through the proxy, keeping the per-name signal in step (verified).
            # Identity, not equality: a rebound function object is a different object with an equal repr.
            setattr(module, key, value)
    # `__load` sets both of these on a successful reload, so a rolled-back namespace must not keep the
    # new file's values. `__flags` is what `cache_across_reloads` reads to decide annotation handling.
    real_dict["__doc__"] = snapshot.doc
    real_dict["_ReactiveModule__flags"] = snapshot.flags
    # Also set here, not only after the batch flush: the flush is what re-marks this dirty, and a
    # caller that never reaches the flush (an escaping exception) still needs the flag settled.
    _load_handle(module).dirty = snapshot.dirty
