"""Ambient pyth-on-line finder, watcher, and request-boundary publisher."""

# This disposable integration deliberately reports candidate errors verbatim.
# pyright: reportMissingImports=false, reportArgumentType=false, reportIndexIssue=false

from __future__ import annotations

import atexit
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import telemetry
from .policy import classify, matches_any, syntax_preflight

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False
_STATE_LOCK = threading.RLock()
_SYNC_LOCK = threading.RLock()
_PENDING: dict[Path, dict[str, Any]] = {}
_SOURCE_ROOT: Path | None = None
_AUTO_PATTERNS: list[str] = []
_LAZY_PATTERNS: list[str] = []
_FORCED_DEPENDENTS: dict[str, list[str]] = {}
_MANIFEST: dict[str, Any] | None = None
_MANIFEST_PATHS: set[str] = set()
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None
_STATUS_DUMP_LOCK = threading.Lock()


def _relative(path: Path) -> str:
    assert _SOURCE_ROOT is not None
    try:
        return path.resolve().relative_to(_SOURCE_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _stop_watcher() -> None:
    _WATCH_STOP.set()
    thread = _WATCH_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


def install_from_env() -> None:
    global _INSTALLED, _SOURCE_ROOT, _AUTO_PATTERNS, _LAZY_PATTERNS, _FORCED_DEPENDENTS
    global _MANIFEST, _MANIFEST_PATHS, _WATCH_THREAD
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        _SOURCE_ROOT = Path(os.environ["HMR_VLLM_SOURCE_ROOT"]).resolve()
        _AUTO_PATTERNS = [p for p in os.getenv("HMR_VLLM_AUTO_PATTERNS", "").split(",") if p]
        _LAZY_PATTERNS = [p for p in os.getenv("HMR_VLLM_LAZY_PATTERNS", "").split(",") if p]
        _FORCED_DEPENDENTS = json.loads(os.getenv("HMR_VLLM_FORCED_DEPENDENTS", "{}"))

        manifest_path = os.getenv("HMR_VLLM_MANIFEST")
        if manifest_path:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            manifest_root = Path(manifest["source_root"]).resolve()
            if manifest_root != _SOURCE_ROOT:
                raise RuntimeError(f"manifest source_root {manifest_root} != configured source root {_SOURCE_ROOT}")
            _MANIFEST_PATHS = {item["path"] for item in manifest["files"]}
            for item in manifest["files"]:
                path = _SOURCE_ROOT / item["path"]
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != item["sha256"]:
                    raise RuntimeError(f"manifest hash mismatch for {item['path']}: {digest} != {item['sha256']}")
            _MANIFEST = manifest

        reactive_relatives = list(_MANIFEST.get("reactive_paths", [])) if _MANIFEST else []
        include_paths = [str((_SOURCE_ROOT / relative).resolve()) for relative in reactive_relatives] if reactive_relatives else [str(_SOURCE_ROOT / "vllm")]

        from reactivity.hmr.core import patch_meta_path

        patch_meta_path(includes=include_paths)
        telemetry.install_profiler()
        telemetry.event(
            "hmr_installed",
            includes=include_paths,
            auto_patterns=_AUTO_PATTERNS,
            lazy_patterns=_LAZY_PATTERNS,
            manifest_path=manifest_path,
            manifest_paths=sorted(_MANIFEST_PATHS),
        )
        _WATCH_THREAD = threading.Thread(target=_watch, name="hmr-vllm-watch", daemon=True)
        _WATCH_THREAD.start()
        atexit.register(_stop_watcher)
        _INSTALLED = True


def _watch() -> None:
    from watchfiles import Change, watch

    assert _SOURCE_ROOT is not None
    try:
        watch_paths = [str(_SOURCE_ROOT / relative) for relative in sorted(_MANIFEST_PATHS)] if _MANIFEST_PATHS else [str(_SOURCE_ROOT)]
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
                    rel = _relative(path)
                    if _MANIFEST_PATHS and rel not in _MANIFEST_PATHS:
                        continue
                    decision = classify(rel)
                    _PENDING[path] = {
                        "path": rel,
                        "seen_at": now,
                        "decision": decision.as_dict(),
                    }
                    telemetry.event("source_change", path=rel, decision=decision.as_dict())
            _dump_status()
    except Exception as exc:
        telemetry.event("watcher_failed", error=f"{type(exc).__name__}: {exc}")
        _dump_status()


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
    """Publish queued files at a request boundary; never called by the watcher."""
    if not _INSTALLED:
        return {"installed": False, "published": [], "rejected": []}
    if telemetry.active_scopes() and not force:
        telemetry.event("publication_deferred", reason="active_http_scopes")
        return {"installed": True, "deferred": True, "published": [], "rejected": []}

    from reactivity.hmr.core import HMR_CONTEXT, get_path_module_map
    from reactivity.hmr.fs import notify
    from reactivity.hmr.hooks import call_post_reload_hooks, call_pre_reload_hooks

    published: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with _SYNC_LOCK:
        with _STATE_LOCK:
            items = list(_PENDING.items())
            _PENDING.clear()
        if not items:
            return {"installed": True, "deferred": False, "published": [], "rejected": []}

        path_map = get_path_module_map()
        by_name = {_module_name(module): module for module in path_map.values()}
        call_pre_reload_hooks()
        try:
            with HMR_CONTEXT.batch():
                for path, record in items:
                    rel = record["path"]
                    allowed = force or matches_any(rel, _AUTO_PATTERNS) or matches_any(rel, _LAZY_PATTERNS)
                    if not allowed:
                        rejected.append({**record, "error": "not allowlisted for automatic publication"})
                        continue
                    if path.suffix == ".py":
                        ok, error = syntax_preflight(path)
                        if not ok:
                            rejected.append({**record, "error": error, "rollback": "old namespace not executed"})
                            continue
                    module = path_map.get(path.resolve())
                    if module is None:
                        notify(path.resolve())
                        published.append({**record, "mode": "resource_notification_or_not_loaded"})
                        continue
                    load = _load_handle(module)
                    load.invalidate()
                    if matches_any(rel, _LAZY_PATTERNS):
                        published.append({**record, "mode": "invalidated_for_late_read"})
                        continue
                    try:
                        load()
                        dirty_dependents = sorted(_module_name(candidate) for candidate in path_map.values() if getattr(_load_handle(candidate), "dirty", False))
                        telemetry.event(
                            "dependency_dirty_set",
                            source=rel,
                            modules=dirty_dependents,
                        )
                        forced_dependents_reexecuted = []
                        for name in _FORCED_DEPENDENTS.get(rel, []):
                            dependent = by_name.get(name)
                            if dependent is None:
                                raise RuntimeError(f"forced dependent {name!r} is not loaded")
                            dependent_load = _load_handle(dependent)
                            dependent_load.invalidate()
                            dependent_load()
                            forced_dependents_reexecuted.append(name)
                        published.append(
                            {
                                **record,
                                "mode": "eager_request_boundary",
                                "dirty_dependents": dirty_dependents,
                                "forced_dependents_reexecuted": forced_dependents_reexecuted,
                            }
                        )
                    except Exception as exc:
                        rejected.append(
                            {
                                **record,
                                "error": f"{type(exc).__name__}: {exc}",
                                "rollback": "not guaranteed; restart required",
                            }
                        )
        finally:
            call_post_reload_hooks()

    for item in published:
        telemetry.event("published", **item)
    for item in rejected:
        telemetry.event("rejected", **item)
    _dump_status()
    return {"installed": True, "deferred": False, "published": published, "rejected": rejected}


def state() -> dict[str, Any]:
    with _STATE_LOCK:
        pending = list(_PENDING.values())
    return {
        "installed": _INSTALLED,
        "pending": pending,
        "manifest": _MANIFEST,
        "telemetry": telemetry.snapshot(),
    }


def _dump_status() -> None:
    root = os.getenv("HMR_VLLM_STATUS_DIR")
    if root:
        with _STATUS_DUMP_LOCK:
            target = Path(root) / f"{os.getpid()}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(state(), sort_keys=True), encoding="utf-8")
            temp.replace(target)
