"""Install pyth-on-line HMR, start watching, and publish at request boundaries.

This never touches vLLM's model weights, compiled kernels, scheduler, or entrypoint.
It only re-executes two Python modules on disk change, nothing else.
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, NamedTuple

# Imported here, not inside the watcher thread: a missing `watchfiles` must fail the
# install instead of dying in a thread and leaving `installed: True` with nothing watched.
from watchfiles import Change, watch

from . import telemetry
from .scope import Manifest, ScopeError, build_manifest, load_manifest, sha256, syntax_preflight
from .telemetry import active_scopes, event

_INSTALL_LOCK = threading.RLock()
_INSTALLED = False
_STATE_LOCK = threading.RLock()
_SYNC_LOCK = threading.RLock()
_PENDING: dict[Path, dict[str, Any]] = {}
_MANIFEST: Manifest | None = None
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None
_WATCHER_FAILED = False
_WATCHER_RESTARTS = 0
_FILE_DIGESTS: dict[str, str] = {}  # relative path -> the content digest this process last accounted for
DEFAULT_MAX_WATCHER_RESTARTS = 3


def _after_fork_child() -> None:
    _INSTALL_LOCK.release()
    if _INSTALLED:
        _after_fork()


# Register the barrier before any install can hold the lock. A fork waits for an in-flight
# install to finish, then the child rebuilds the watcher and locks inherited without threads.
if register_at_fork := getattr(os, "register_at_fork", None):
    register_at_fork(before=lambda: _INSTALL_LOCK.acquire(), after_in_parent=lambda: _INSTALL_LOCK.release(), after_in_child=_after_fork_child)


def max_watcher_restarts() -> int:
    """How many times a dead watcher may be restarted, overridable via `HMR_VLLM_MAX_WATCHER_RESTARTS`.

    Bounded on purpose: a watcher failing for a reason that is still true (an unmounted source
    root) would otherwise be restarted once per request forever. `0` disables recovery, making the
    first failure terminal. A negative or unparsable value falls back to the default.
    """
    raw = os.getenv("HMR_VLLM_MAX_WATCHER_RESTARTS")
    if not raw:
        return DEFAULT_MAX_WATCHER_RESTARTS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_WATCHER_RESTARTS
    return value if value >= 0 else DEFAULT_MAX_WATCHER_RESTARTS


def _is_model_registry_inspector() -> bool:
    """vLLM's short-lived subprocess that inspects model architectures must not leave a watcher."""
    try:
        command = Path("/proc/self/cmdline").read_bytes()
    except OSError:
        return False
    return b"vllm.model_executor.models.registry" in command


def install_unless_registry_inspector() -> None:
    """The default `--hmr-runtime` entrypoint: guard against vLLM's architecture probe."""
    if not _is_model_registry_inspector():
        install_from_env()


def install_from_env() -> None:
    global _INSTALLED, _MANIFEST, _WATCH_THREAD, _WATCHER_FAILED, _WATCHER_RESTARTS
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        source_root_raw = os.environ.get("HMR_VLLM_SOURCE_ROOT")
        if not source_root_raw:
            raise RuntimeError("HMR_VLLM_SOURCE_ROOT is required but not set")
        source_root = Path(source_root_raw).resolve()
        manifest_path_raw = os.getenv("HMR_VLLM_MANIFEST")
        manifest = load_manifest(Path(manifest_path_raw), source_root) if manifest_path_raw else build_manifest(source_root)
        include_paths = [str((source_root / relative).resolve()) for relative in manifest.reactive_paths]

        from reactivity.hmr import fs
        from reactivity.hmr.core import patch_meta_path

        previous_finders = {id(finder) for finder in sys.meta_path}
        previous_filters = {id(path_filter) for path_filter in fs._filters}  # noqa: SLF001 - hmr exposes registration but no removal API
        attempt_finders: set[int] = set()
        attempt_filters: set[int] = set()
        exit_registered = False
        _MANIFEST = manifest
        try:
            try:
                patch_meta_path(includes=include_paths)
            finally:
                # Capture only the hook registration step, so later registrations survive rollback.
                attempt_finders = {id(finder) for finder in sys.meta_path} - previous_finders
                attempt_filters = {id(path_filter) for path_filter in fs._filters} - previous_filters  # noqa: SLF001
            # Baseline for the rescan a watcher restart performs: edits made while no watcher
            # was running must be distinguishable from files nobody has touched.
            _reset_digest_baseline(manifest)
            _start_watcher()
            atexit.register(_stop_watcher)
            exit_registered = True
        except BaseException:
            _stop_watcher()
            if exit_registered:
                atexit.unregister(_stop_watcher)
            # A retry can select another root. Leaving this attempt's finder installed would
            # make the old root reactive even though the next watcher only watches the new one.
            sys.meta_path[:] = [finder for finder in sys.meta_path if id(finder) not in attempt_finders]
            fs._filters[:] = [path_filter for path_filter in fs._filters if id(path_filter) not in attempt_filters]  # noqa: SLF001
            with _STATE_LOCK:
                _MANIFEST = None
                _WATCH_THREAD = None
                _WATCHER_FAILED = False
                _WATCHER_RESTARTS = 0
                _FILE_DIGESTS.clear()
                _PENDING.clear()
            raise
        _INSTALLED = True
        event("hmr_installed", includes=include_paths, manifest_paths=list(manifest.reactive_paths))


def _start_watcher() -> None:
    global _WATCH_STOP, _WATCH_THREAD
    _WATCH_STOP = threading.Event()
    _WATCH_THREAD = threading.Thread(target=_watch, name="vllm-hmr-watch", daemon=True)
    _WATCH_THREAD.start()


def _digests_on_disk(manifest: Manifest) -> list[tuple[str, str, Path]]:
    """Hash every watched file, omitting the ones that cannot be read.

    Omitted rather than raising: both callers run on the recovery path, where a source root that
    has gone away is exactly the failure being recovered from, and a digest that cannot be taken
    must not turn recovery into a second exception. Omitting also needs no sentinel — a missing
    entry compares unequal to any later digest, so a file that becomes readable again is caught by
    the same test that catches an edit.

    Hashing happens here, outside `_STATE_LOCK`, on purpose. That lock is what the watcher thread
    takes to record a change, so holding it across one `read_bytes` per watched file would block the
    thread recovery has just restarted — on a slow or stalled filesystem, exactly the case recovery
    runs in, for as long as the read takes.
    """
    digests: list[tuple[str, str, Path]] = []
    for relative in manifest.reactive_paths:
        path = manifest.path_for(relative)
        try:
            digests.append((relative, sha256(path), path.resolve()))
        except (ScopeError, OSError):
            continue
    return digests


def _reset_digest_baseline(manifest: Manifest) -> None:
    """Record the on-disk content of every watched file as already accounted for."""
    digests = _digests_on_disk(manifest)
    with _STATE_LOCK:
        _FILE_DIGESTS.clear()
        _FILE_DIGESTS.update({relative: digest for relative, digest, _ in digests})


def _rescan_after_gap(manifest: Manifest) -> list[str]:
    """Queue every watched file whose content no longer matches the baseline.

    This is what makes a restart honest. While no watcher was running, nothing observed the
    filesystem, and an edit made in that window will never be announced again: the file is not
    touched a second time and `watch()` reports changes from the moment it starts, not from the
    moment the previous one died. Comparing content is the only way to notice those edits, so the
    gap costs one hash per watched file instead of a silently missed publication.

    A digest taken before the lock can only be stale in the direction that is already handled: an
    edit landing between the hash and the compare is one the restarted watcher observes and queues
    itself, and `setdefault` below keeps that fresher record.
    """
    digests = _digests_on_disk(manifest)
    missed: list[str] = []
    now = time.monotonic()
    with _STATE_LOCK:
        for relative, digest, resolved in digests:
            if _FILE_DIGESTS.get(relative) != digest:
                _FILE_DIGESTS[relative] = digest
                # `setdefault`: a record the watcher queued for this path is newer than this one.
                _PENDING.setdefault(resolved, {"path": relative, "seen_at": now, "source": "rescan"})
                missed.append(relative)
    return missed


def _recovery_exhausted(*, watcher_failed: bool, restarts: int) -> bool:
    """Whether the watcher is dead for good: failed, with the restart budget spent.

    One predicate, because `sync_pending` refuses to publish on it and `state()` reports it, and
    two copies of `>=` against a budget the environment can change would be free to disagree.

    `restarts` counts *consecutive* failed recoveries, not a process lifetime total: see
    `_recover_watcher`, which zeroes it once a boundary observes a watcher that held.
    """
    return watcher_failed and restarts >= max_watcher_restarts()


def _watcher_needs_recovery() -> bool:
    """Whether nothing is currently observing the filesystem on our behalf.

    Two distinct ways that happens, and the flag alone only covers the first. `_watch` raising
    sets `_WATCHER_FAILED`; a thread that ends without raising — `watch()` returning on its own,
    or the thread dying for a reason it never got to report — leaves the flag `False` while the
    process is just as blind. `_WATCH_STOP` distinguishes both from the deliberate shutdown
    `_stop_watcher` performs at exit and before a restart, which must not be "recovered" from.
    """
    with _STATE_LOCK:
        if _WATCHER_FAILED:
            return True
    if _WATCH_STOP.is_set():
        return False
    thread = _WATCH_THREAD
    return thread is not None and not thread.is_alive()


def _recover_watcher() -> dict[str, Any]:
    """Restart a watcher that is no longer watching and queue whatever changed while it was gone.

    Called from the request boundary, never from the watcher thread: the thread that died cannot
    restart itself, and doing this at the boundary means recovery happens on the same path that
    publishes, under the same lock discipline. Returns the outcome so the caller can report it
    instead of guessing.
    """
    global _WATCHER_FAILED, _WATCHER_RESTARTS
    # Checked before `_INSTALL_LOCK`: this runs at every request boundary, and the healthy answer
    # must not serialise boundaries behind the lock `install_from_env` holds.
    if not _watcher_needs_recovery():
        with _STATE_LOCK:
            # A boundary that observes a live watcher is proof the previous recovery held, so the
            # budget is handed back here. Without this the counter is a process-lifetime total: a
            # server that recovered three *transient* stalls (an NFS hiccup, an editor's atomic
            # rename storm) days apart would refuse to recover the fourth and disable HMR for good
            # while its watcher was healthy the entire time in between. The budget exists to stop a
            # cause that is *still true* from being retried once per request, and that case is
            # unaffected: a watcher dying before any boundary sees it alive never reaches this line,
            # so consecutive failures still exhaust it.
            _WATCHER_RESTARTS = 0
            return {"attempted": False, "recovered": True, "restarts": 0, "missed": []}
    with _INSTALL_LOCK:
        with _STATE_LOCK:
            if not _watcher_needs_recovery():  # another boundary recovered it while this one waited
                return {"attempted": False, "recovered": True, "restarts": _WATCHER_RESTARTS, "missed": []}
            budget = max_watcher_restarts()
            if budget <= _WATCHER_RESTARTS:
                # Terminal: the failure has outlived its budget, so this stays fail-closed rather
                # than restarting once per request against a cause that is evidently still true.
                return {"attempted": False, "recovered": False, "exhausted": True, "restarts": _WATCHER_RESTARTS, "missed": []}
            _WATCHER_RESTARTS += 1
            attempt = _WATCHER_RESTARTS
        manifest = _MANIFEST
        assert manifest is not None
        _stop_watcher()  # the dead thread may still be joinable; a stale `_WATCH_STOP` must not stop the new one
        missed = _rescan_after_gap(manifest)
        with _STATE_LOCK:
            _WATCHER_FAILED = False  # cleared before the start: a thread that dies immediately sets it again
        _start_watcher()
        thread = _WATCH_THREAD
        alive = thread is not None and thread.is_alive()
        with _STATE_LOCK:
            failed_again = _WATCHER_FAILED
        recovered = alive and not failed_again
        event("watcher_recovery", attempt=attempt, budget=budget, recovered=recovered, missed=missed)
        return {"attempted": True, "recovered": recovered, "restarts": attempt, "missed": missed}


def _after_fork() -> None:
    """A forked child inherits `_INSTALLED` but not the watcher thread, nor unlocked locks.

    Without this, a fork-mode worker reports HMR as installed while nothing is ever
    queued (its watcher does not exist), and any lock a parent thread held at fork time
    stays locked forever. vLLM's `serve` defaults to spawn, where this never runs.
    """
    global _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK, _WATCHER_FAILED, _WATCHER_RESTARTS
    _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK = threading.RLock(), threading.RLock(), threading.RLock()
    _WATCHER_FAILED = False  # the child starts its own watcher below, so a parent's failure is not inherited
    _WATCHER_RESTARTS = 0  # and neither is the parent's spent recovery budget
    telemetry.reset_after_fork()
    _start_watcher()
    event("watcher_restarted_after_fork", pid=os.getpid())


def _stop_watcher() -> None:
    _WATCH_STOP.set()
    thread = _WATCH_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


def _watch() -> None:
    global _WATCHER_FAILED
    assert _MANIFEST is not None
    source_root = _MANIFEST.source_root
    try:
        watch_paths = [str(source_root / relative) for relative in sorted(_MANIFEST.reactive_paths)]
        for changes in watch(
            *watch_paths,
            debounce=int(os.getenv("HMR_VLLM_DEBOUNCE_MS", "300")),
            step=50,
            stop_event=_WATCH_STOP,
        ):
            now = time.monotonic()
            observed: list[tuple[Path, str, str]] = []
            for change, raw in changes:
                if change is Change.deleted:
                    continue
                path = Path(raw).resolve()
                try:
                    rel = path.relative_to(source_root).as_posix()
                except ValueError:
                    continue
                if rel not in _MANIFEST.reactive_paths:
                    continue
                try:
                    digest = sha256(path)
                except (ScopeError, OSError):
                    continue
                observed.append((path, rel, digest))
            with _STATE_LOCK:
                for path, rel, digest in observed:
                    if _FILE_DIGESTS.get(rel) == digest:
                        continue
                    _FILE_DIGESTS[rel] = digest
                    _PENDING[path] = {"path": rel, "seen_at": now}
                    event("source_change", path=rel)
        if not _WATCH_STOP.is_set():
            raise RuntimeError("watcher iterator ended unexpectedly")
    except Exception as exc:
        with _STATE_LOCK:
            _WATCHER_FAILED = True
        event("watcher_failed", error=f"{type(exc).__name__}: {exc}")


def _load_handle(module):
    """Access the intentionally private loader from a core-owned frame."""
    from reactivity.hmr import core

    helper = getattr(core, "_hmr_probe_load", None)
    if helper is None:
        namespace = core.__dict__
        exec("def _hmr_probe_load(module):\n    return module.load\n", namespace)
        helper = namespace["_hmr_probe_load"]
    return helper(module)


def _module_name(module) -> str:
    return object.__getattribute__(module, "__name__")


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


def sync_pending(*, force: bool = False) -> dict[str, Any]:
    """Publish queued changes at a request boundary. Never invoked by the watcher thread."""
    if not _INSTALLED:
        # `hmr_available` on every return, including this one: the middleware decides whether to fan
        # out to the workers from this field, and a missing key reads as `None`, which is neither
        # `True` nor `False`. An uninstalled API process must not ask workers to publish.
        return {"installed": False, "hmr_available": False, "published": [], "rejected": []}
    # Recovery first, and before the deferral check: a dead watcher queues nothing, so a boundary
    # that skipped this would publish an empty queue and report success while every later source
    # edit went unobserved. `_recover_watcher` is a no-op when the watcher is healthy.
    recovery = _recover_watcher()
    with _STATE_LOCK:
        watcher_failed = _WATCHER_FAILED
        restarts = _WATCHER_RESTARTS

    def result(*, hmr_available: bool = True, **extra: Any) -> dict[str, Any]:
        """One shape for every return below, so no exit can omit a field the middleware reads."""
        return {"installed": True, "hmr_available": hmr_available, "watcher_failed": watcher_failed, "watcher_recovery": recovery, "published": [], "rejected": [], **extra}

    # Recovery budget exhausted: the watcher has died more times than the configured limit allows,
    # proving the cause is still true (an unmounted source root, a missing dependency). This is
    # terminal: the process can no longer observe filesystem changes, so claiming HMR works or
    # attempting to publish queued items would be a lie. Fail closed, leave the queue undrained.
    if _recovery_exhausted(watcher_failed=watcher_failed, restarts=restarts):
        event("publication_refused", reason="watcher_recovery_exhausted", restarts=restarts)
        return result(hmr_available=False)
    if active_scopes() and not force:
        event("publication_deferred", reason="active_http_scopes")
        return result(deferred=True)

    from reactivity.hmr.core import HMR_CONTEXT, get_path_module_map
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    published: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    settled: set[Path] = set()  # decided paths, so an escaping exception knows which items are still owed a decision
    # Modules whose namespace was rolled back, mapped to the `dirty` each must end on. Restoring
    # inside the batch is not enough: writing the old values back through the proxy notifies the
    # reverted names, and the flush on batch exit re-marks their subscribers dirty. A module left
    # dirty re-executes the still-broken file on the next attribute read, which is exactly the
    # half-published state rollback exists to prevent, so the flag is settled again after the flush.
    # Keyed by the module itself: `ReactiveModule` defines neither `__eq__` nor `__hash__`, so it
    # hashes by identity, which is the same distinction `id()` would draw without the indirection.
    rolled_back: dict[Any, bool] = {}
    with _SYNC_LOCK:
        with _STATE_LOCK:
            items = list(_PENDING.items())
            _PENDING.clear()
        if not items:
            return result(deferred=False)

        assert _MANIFEST is not None
        path_map = get_path_module_map()
        by_name = {_module_name(module): module for module in path_map.values()}
        try:
            # Inside the `try`: a raising pre-hook must still run the post-hooks, and must not
            # swallow the items this call already drained out of `_PENDING`.
            call_pre_reload_hooks()
            with HMR_CONTEXT.batch():
                for path, record in items:
                    rel = record["path"]
                    if rel not in _MANIFEST.auto_paths and not force:
                        rejected.append({**record, "error": "not in manifest auto_paths"})
                        settled.add(path)
                        continue
                    if path.suffix == ".py":
                        ok, error = syntax_preflight(path)
                        if not ok:
                            rejected.append({**record, "error": error})
                            settled.add(path)
                            continue
                    module = path_map.get(path.resolve())
                    if module is None:
                        # Every in-scope path is a Python module this process must have imported.
                        # Not being in the map means the live `vllm` came from somewhere else (a
                        # site-packages install while the source root is only a copy), so calling
                        # this "published" would claim a swap that never touched running code.
                        rejected.append({**record, "error": "not loaded from this source root: the running process imported this module from elsewhere"})
                        settled.add(path)
                        continue
                    load = _load_handle(module)
                    # Snapshot namespace and private state BEFORE any invalidation, so rollback can restore the pre-transaction state.
                    snapshot = _snapshot_module_state(module)
                    dependents_snapshots: list[tuple[object, _ModuleSnapshot]] = []
                    for name in _MANIFEST.forced_dependents.get(rel, ()):
                        if dependent := by_name.get(name):
                            dependents_snapshots.append((dependent, _snapshot_module_state(dependent)))
                    load.invalidate()
                    try:
                        load()
                        dirty_dependents = sorted(_module_name(candidate) for candidate in path_map.values() if getattr(_load_handle(candidate), "dirty", False))
                        forced_dependents_reexecuted = []
                        for name in _MANIFEST.forced_dependents.get(rel, ()):
                            dependent = by_name.get(name)
                            if dependent is None:
                                raise RuntimeError(f"forced dependent {name!r} is not loaded")
                            dependent_load = _load_handle(dependent)
                            dependent_load.invalidate()
                            dependent_load()
                            forced_dependents_reexecuted.append(name)
                        published.append({**record, "mode": "eager", "dirty_dependents": dirty_dependents, "forced_dependents_reexecuted": forced_dependents_reexecuted})
                        settled.add(path)
                        # This item re-executed these modules successfully, so an earlier item's rollback
                        # of them no longer describes their state and must not be re-applied after the flush.
                        rolled_back.pop(module, None)
                        for name in forced_dependents_reexecuted:
                            if (reexecuted := by_name.get(name)) is not None:
                                rolled_back.pop(reexecuted, None)
                    except Exception as exc:
                        # Target or forced dependent load failed: rollback all affected modules to their pre-transaction state.
                        _restore_module_state(module, snapshot)
                        rolled_back[module] = snapshot.dirty
                        for dependent, dep_snapshot in dependents_snapshots:
                            _restore_module_state(dependent, dep_snapshot)
                            rolled_back[dependent] = dep_snapshot.dirty
                        rejected.append({**record, "error": f"{type(exc).__name__}: {exc}"})
                        settled.add(path)
        except BaseException:
            # A raising pre-hook aborts before any item is looked at, and these items were already
            # drained out of `_PENDING`. Nothing on disk will re-announce them — the file is not
            # touched again and no new watcher event fires — so an undecided item must go back on
            # the queue or the edit is lost for the lifetime of the process.
            with _STATE_LOCK:
                for path, record in items:
                    if path not in settled:
                        # `setdefault`: a watcher record queued since the drain describes the file
                        # as it is now, and must not be replaced by this older one.
                        _PENDING.setdefault(path, record)
            raise
        finally:
            # The batch flush re-marks subscribers of reverted names dirty. A module rolled back to
            # a good state but left dirty will re-execute the still-broken file on the next attribute
            # read, which is the half-published namespace rollback prevents, so settle the flag again.
            # No `hasattr` guard: every module in here went through `_restore_module_state`, which
            # already assigned `load.dirty`, so the attribute is there by construction.
            for module_obj, snapshot_dirty in rolled_back.items():
                _load_handle(module_obj).dirty = snapshot_dirty
            call_post_reload_hooks()

    # Decided items are accounted for, so the rescan baseline must move to what is on disk now.
    # Published and rejected alike: a rejected file that a later rescan re-queued unchanged would
    # be re-attempted at every boundary, failing the same way each time.
    assert _MANIFEST is not None  # still holds; the prior assert survived exception paths
    for item in (*published, *rejected):
        relative = item.get("path")
        if not isinstance(relative, str) or relative not in _MANIFEST.reactive_paths:
            continue
        try:
            digest = sha256(_MANIFEST.path_for(relative))
        except ScopeError:
            continue
        with _STATE_LOCK:
            _FILE_DIGESTS[relative] = digest
    for item in published:
        event("published", **item)
    for item in rejected:
        event("rejected", **item)
    return result(deferred=False, published=published, rejected=rejected)


def state() -> dict[str, Any]:
    with _STATE_LOCK:
        pending = list(_PENDING.values())
        watcher_failed = _WATCHER_FAILED
        restarts = _WATCHER_RESTARTS
    thread = _WATCH_THREAD
    from .telemetry import snapshot

    return {
        "installed": _INSTALLED,
        "watcher_failed": watcher_failed,
        "watcher_alive": bool(thread is not None and thread.is_alive()),
        "watcher_restarts": restarts,
        "watcher_recovery_exhausted": _recovery_exhausted(watcher_failed=watcher_failed, restarts=restarts),
        "pending": pending,
        "manifest": _MANIFEST.as_dict() if _MANIFEST else None,
        "telemetry": snapshot(),
    }
