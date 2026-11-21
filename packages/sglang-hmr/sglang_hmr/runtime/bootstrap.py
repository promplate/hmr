"""Install pyth-on-line HMR, start watching, and publish from watcher thread.

This never touches SGLang's model weights, compiled kernels, scheduler, or entrypoint.
It only re-executes two Python modules on disk change, nothing else.

Unlike vLLM, SGLang has no middleware/worker-extension hooks for request-boundary
publication. The watcher thread publishes directly after syntax preflight, with no
in-flight interlock. The example waits for publication to complete before the
next real API request observes new code.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchfiles import Change, watch

from . import telemetry
from .scope import Manifest, build_manifest, load_manifest, syntax_preflight
from .telemetry import event

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_STATE_LOCK = threading.RLock()
_SYNC_LOCK = threading.RLock()
_PENDING: dict[Path, dict[str, Any]] = {}
_MANIFEST: Manifest | None = None
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None
_WATCHER_ERROR: str | None = None


def install() -> None:
    global _INSTALLED, _MANIFEST
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        source_root_raw = os.environ.get("HMR_SGLANG_SOURCE_ROOT")
        if not source_root_raw:
            raise RuntimeError("HMR_SGLANG_SOURCE_ROOT is required but not set")
        source_root = Path(source_root_raw).resolve()
        manifest_path_raw = os.getenv("HMR_SGLANG_MANIFEST")
        manifest = load_manifest(Path(manifest_path_raw), source_root) if manifest_path_raw else build_manifest(source_root)
        _MANIFEST = manifest

        include_paths = [str((source_root / relative).resolve()) for relative in manifest.reactive_paths]

        from reactivity.hmr.core import patch_meta_path

        patch_meta_path(includes=include_paths)

        event("hmr_installed", includes=include_paths, manifest_paths=list(manifest.reactive_paths))
        _start_watcher()
        atexit.register(_stop_watcher)
        os.register_at_fork(after_in_child=_after_fork)
        _INSTALLED = True


def _start_watcher() -> None:
    global _WATCH_STOP, _WATCH_THREAD
    _WATCH_STOP = threading.Event()
    _WATCH_THREAD = threading.Thread(target=_watch, name="sglang-hmr-watch", daemon=True)
    _WATCH_THREAD.start()


def _after_fork() -> None:
    """A forked child inherits `_INSTALLED` but not the watcher thread, nor unlocked locks."""
    global _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK, _WATCHER_ERROR
    _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK = threading.Lock(), threading.RLock(), threading.RLock()
    _WATCHER_ERROR = None  # the parent's failure says nothing about this child's fresh watcher
    telemetry.reset_after_fork()
    _start_watcher()
    event("watcher_restarted_after_fork", pid=os.getpid())


def _stop_watcher() -> None:
    _WATCH_STOP.set()
    thread = _WATCH_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


def _watch() -> None:
    global _WATCHER_ERROR
    assert _MANIFEST is not None
    source_root = _MANIFEST.source_root
    try:
        watch_paths = [str(source_root / relative) for relative in sorted(_MANIFEST.reactive_paths)]
        for changes in watch(
            *watch_paths,
            debounce=int(os.getenv("HMR_SGLANG_DEBOUNCE_MS", "300")),
            step=50,
            stop_event=_WATCH_STOP,
        ):
            now = time.monotonic()
            with _STATE_LOCK:
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
                    _PENDING[path] = {"path": rel, "seen_at": now}
                    event("source_change", path=rel)
            sync_pending()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _WATCHER_ERROR = error
        event("watcher_failed", error=error)


def _load_handle(module):
    """The loader is private on purpose; `uvicorn-hmr` reaches it by the same name-mangled route.

    Read off the class, not the instance: on an instance the mangled name resolves against the
    module's own namespace dict instead of the descriptor.
    """
    from reactivity.hmr.core import ReactiveModule

    descriptor = ReactiveModule.__load if TYPE_CHECKING else ReactiveModule._ReactiveModule__load  # noqa: SLF001
    return descriptor.__get__(module, ReactiveModule)


def _module_origin(module) -> Path | None:
    """The live module's own `__file__`, which is the only proof of where its code came from.

    `__file__` and `__name__` are in the loader's `STATIC_ATTRS`, so plain attribute access
    already bypasses the reactive proxy and records no dependency.
    """
    file = getattr(module, "__file__", None)
    return None if file is None else Path(file).resolve()


def sync_pending() -> dict[str, Any]:
    """Publish queued changes from watcher thread. No request-boundary interlock."""
    if not _INSTALLED:
        return {"installed": False, "published": [], "rejected": []}

    from reactivity.hmr.core import HMR_CONTEXT, get_path_module_map
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    published: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with _SYNC_LOCK:
        with _STATE_LOCK:
            items = list(_PENDING.items())
            _PENDING.clear()
        if not items:
            return {"installed": True, "published": [], "rejected": []}

        assert _MANIFEST is not None
        path_map = get_path_module_map()
        by_name = {module.__name__: module for module in path_map.values()}

        # A pre-hook that raises means user code never prepared for this reload, so nothing may
        # be published. The drained items go back to `_PENDING` so the next watcher pass retries
        # them, instead of the edit being silently dropped while the state still looks healthy.
        try:
            call_pre_reload_hooks()
        except Exception as exc:
            error = f"pre-reload hook failed: {type(exc).__name__}: {exc}"
            _requeue(items)
            event("rejected", error=error, paths=[record["path"] for _, record in items])
            return {"installed": True, "published": [], "rejected": [{**record, "error": error} for _, record in items]}

        retry: list[tuple[Path, dict[str, Any]]] = []
        try:
            with HMR_CONTEXT.batch():
                for path, record in items:
                    rel = record["path"]
                    if rel not in _MANIFEST.auto_paths:
                        # Not a retryable condition: the manifest, not the file, is what forbids this.
                        rejected.append({**record, "error": "not in manifest auto_paths"})
                        continue
                    if path.suffix == ".py":
                        ok, error = syntax_preflight(path)
                        if not ok:
                            # A half-written file: keep it pending so the finished write republishes.
                            retry.append((path, record))
                            rejected.append({**record, "error": error, "retryable": True})
                            continue
                    resolved = path.resolve()
                    module = path_map.get(resolved)
                    if module is None:
                        # The live module came from somewhere else entirely. Publishing against
                        # this tree would report success while the process keeps the old code, so
                        # this stays a hard refusal rather than a retry.
                        rejected.append({**record, "error": "not loaded from this source root: the running process imported this module from elsewhere"})
                        continue
                    origin = _module_origin(module)
                    if origin != resolved:
                        # The map key matched but the live module's own `__file__` points elsewhere,
                        # e.g. a site-packages copy shadowing the watched tree. Re-executing it
                        # would publish this file's bytes into a module the request path never uses.
                        rejected.append({**record, "error": f"live module origin {origin} does not match the watched source root path {path}"})
                        continue

                    # Before invalidating the provider, verify all its forced dependents are loaded
                    # and came from the same source root. Publishing a new provider without re-executing
                    # its from-import consumer would leave the old function live.
                    forced_dependent_names = _MANIFEST.forced_dependents.get(rel, ())
                    rejected_this = False
                    for name in forced_dependent_names:
                        dependent = by_name.get(name)
                        if dependent is None:
                            rejected.append({**record, "error": f"forced dependent {name!r} is not loaded"})
                            rejected_this = True
                            break
                        dependent_origin = _module_origin(dependent)
                        if dependent_origin is None:
                            rejected.append({**record, "error": f"forced dependent {name!r} has no __file__"})
                            rejected_this = True
                            break
                        try:
                            dependent_origin.relative_to(_MANIFEST.source_root)
                        except ValueError:
                            rejected.append({**record, "error": f"forced dependent {name!r} origin {dependent_origin} is not under source root {_MANIFEST.source_root}"})
                            rejected_this = True
                            break
                    if rejected_this:
                        continue

                    # All forced dependents passed: now reload the provider and then each dependent.
                    load = _load_handle(module)
                    load.invalidate()
                    try:
                        load()
                        dirty_dependents = sorted(candidate.__name__ for candidate in path_map.values() if getattr(_load_handle(candidate), "dirty", False))
                        forced_dependents_reexecuted = []
                        for name in forced_dependent_names:
                            dependent = by_name[name]  # already verified above
                            dependent_load = _load_handle(dependent)
                            dependent_load.invalidate()
                            dependent_load()
                            forced_dependents_reexecuted.append(name)
                        published.append({**record, "mode": "eager", "dirty_dependents": dirty_dependents, "forced_dependents_reexecuted": forced_dependents_reexecuted})
                    except Exception as exc:
                        # The module (or a forced dependent) raised while re-executing. The loader
                        # is now holding a failed module, so this is not "carry on": keep it
                        # pending and report it, so a fixed file gets another attempt.
                        retry.append((path, record))
                        rejected.append({**record, "error": f"{type(exc).__name__}: {exc}", "retryable": True})
        finally:
            if retry:
                _requeue(retry)
            # Post-hooks always run, so user code that took a lock in the pre-hook can release it.
            # A raising post-hook must not erase the publication that already happened, so its
            # failure is reported as its own event rather than reclassifying anything above.
            try:
                call_post_reload_hooks()
            except Exception as exc:
                event("post_reload_hook_failed", error=f"{type(exc).__name__}: {exc}")

    for item in published:
        event("published", **item)
    for item in rejected:
        event("rejected", **item)
    return {"installed": True, "published": published, "rejected": rejected}


def _requeue(items: list[tuple[Path, dict[str, Any]]]) -> None:
    """Put drained changes back, without clobbering a newer edit the watcher saw meanwhile."""
    with _STATE_LOCK:
        for path, record in items:
            _PENDING.setdefault(path, record)


def state() -> dict[str, Any]:
    with _STATE_LOCK:
        pending = list(_PENDING.values())
    thread = _WATCH_THREAD
    watching = thread is not None and thread.is_alive()
    # `installed` alone would still read True after the watcher thread died, i.e. a process that
    # can no longer see any edit would look healthy. `healthy` is what a caller should assert on.
    return {
        "installed": _INSTALLED,
        "watching": watching,
        "watcher_error": _WATCHER_ERROR,
        "healthy": _INSTALLED and watching and _WATCHER_ERROR is None,
        "pending": pending,
        "manifest": _MANIFEST.as_dict() if _MANIFEST else None,
        "telemetry": telemetry.snapshot(),
    }
