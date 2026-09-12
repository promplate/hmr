"""Install pyth-on-line HMR, start watching, and publish at request boundaries.

This never touches vLLM's model weights, compiled kernels, scheduler, or entrypoint.
It only re-executes two Python modules on disk change, nothing else.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from pathlib import Path
from typing import Any

# Imported here, not inside the watcher thread: a missing `watchfiles` must fail the
# install instead of dying in a thread and leaving `installed: True` with nothing watched.
from watchfiles import Change, watch

from . import telemetry
from .scope import Manifest, build_manifest, load_manifest, syntax_preflight
from .telemetry import active_scopes, event

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_STATE_LOCK = threading.RLock()
_SYNC_LOCK = threading.RLock()
_PENDING: dict[Path, dict[str, Any]] = {}
_MANIFEST: Manifest | None = None
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None


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
    global _INSTALLED, _MANIFEST, _WATCH_THREAD
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        source_root_raw = os.environ.get("HMR_VLLM_SOURCE_ROOT")
        if not source_root_raw:
            raise RuntimeError("HMR_VLLM_SOURCE_ROOT is required but not set")
        source_root = Path(source_root_raw).resolve()
        manifest_path_raw = os.getenv("HMR_VLLM_MANIFEST")
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
    _WATCH_THREAD = threading.Thread(target=_watch, name="vllm-hmr-watch", daemon=True)
    _WATCH_THREAD.start()


def _after_fork() -> None:
    """A forked child inherits `_INSTALLED` but not the watcher thread, nor unlocked locks.

    Without this, a fork-mode worker reports HMR as installed while nothing is ever
    queued (its watcher does not exist), and any lock a parent thread held at fork time
    stays locked forever. vLLM's `serve` defaults to spawn, where this never runs.
    """
    global _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK
    _INSTALL_LOCK, _STATE_LOCK, _SYNC_LOCK = threading.Lock(), threading.RLock(), threading.RLock()
    telemetry.reset_after_fork()
    _start_watcher()
    event("watcher_restarted_after_fork", pid=os.getpid())


def _stop_watcher() -> None:
    _WATCH_STOP.set()
    thread = _WATCH_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


def _watch() -> None:
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
    except Exception as exc:
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


def sync_pending(*, force: bool = False) -> dict[str, Any]:
    """Publish queued changes at a request boundary. Never invoked by the watcher thread."""
    if not _INSTALLED:
        return {"installed": False, "published": [], "rejected": []}
    if active_scopes() and not force:
        event("publication_deferred", reason="active_http_scopes")
        return {"installed": True, "deferred": True, "published": [], "rejected": []}

    from reactivity.hmr.core import HMR_CONTEXT, get_path_module_map
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    published: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with _SYNC_LOCK:
        with _STATE_LOCK:
            items = list(_PENDING.items())
            _PENDING.clear()
        if not items:
            return {"installed": True, "deferred": False, "published": [], "rejected": []}

        assert _MANIFEST is not None
        path_map = get_path_module_map()
        by_name = {_module_name(module): module for module in path_map.values()}
        call_pre_reload_hooks()
        try:
            with HMR_CONTEXT.batch():
                for path, record in items:
                    rel = record["path"]
                    if rel not in _MANIFEST.auto_paths and not force:
                        rejected.append({**record, "error": "not in manifest auto_paths"})
                        continue
                    if path.suffix == ".py":
                        ok, error = syntax_preflight(path)
                        if not ok:
                            rejected.append({**record, "error": error})
                            continue
                    module = path_map.get(path.resolve())
                    if module is None:
                        # Every in-scope path is a Python module this process must have imported.
                        # Not being in the map means the live `vllm` came from somewhere else (a
                        # site-packages install while the source root is only a copy), so calling
                        # this "published" would claim a swap that never touched running code.
                        rejected.append({**record, "error": "not loaded from this source root: the running process imported this module from elsewhere"})
                        continue
                    load = _load_handle(module)
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
                    except Exception as exc:
                        rejected.append({**record, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            call_post_reload_hooks()

    for item in published:
        event("published", **item)
    for item in rejected:
        event("rejected", **item)
    return {"installed": True, "deferred": False, "published": published, "rejected": rejected}


def state() -> dict[str, Any]:
    with _STATE_LOCK:
        pending = list(_PENDING.values())
    from .telemetry import snapshot

    return {
        "installed": _INSTALLED,
        "pending": pending,
        "manifest": _MANIFEST.as_dict() if _MANIFEST else None,
        "telemetry": snapshot(),
    }
