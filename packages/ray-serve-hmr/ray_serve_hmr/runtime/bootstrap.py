"""Watch the replica request path and publish it at a safe request boundary.

Called from inside the replica actor process (see `ray_serve_hmr.install`). Nothing here
touches the deployment's model, the deployment config, the number of replicas, or the actor:
it re-executes one Python module already loaded in this process and rebinds the live objects
that would otherwise keep the old code (see `publish.rebind_live_instances`).

Publication safety. The watcher thread only enqueues. The actual reload runs on the replica's
main event loop -- the loop that dispatches `handle_request*` -- so it cannot interleave with
the dispatch of a request. It additionally waits until the replica reports zero ongoing
requests, so a request already inside user code is never served by half-old, half-new code:
that in-flight request finishes on the old implementation, and the reload happens after it.

Failure. A syntax error is caught before the loader sees it (`ReactiveModule.__load` routes
`SyntaxError` to `sys.excepthook` and then leaves the module half-updated with no way to tell).
A runtime error during re-execution is caught, and the live instances are left bound to the old
classes, so the replica keeps serving. Either way the change stays pending and a fixed file
gets another attempt.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import threading
import time
from pathlib import Path
from typing import Any

from watchfiles import Change, watch

from . import publish
from .scope import TARGET_PATH, Manifest, build_manifest, resolve_source_root

_INSTALL_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()
_PUBLISH_LOCK = threading.RLock()

_INSTALLED = False
_MANIFEST: Manifest | None = None
_MODULE: Any = None
_LOOP: asyncio.AbstractEventLoop | None = None
_PENDING: dict[Path, dict[str, Any]] = {}
_EVENTS: list[dict[str, Any]] = []
_WATCH_STOP = threading.Event()
_WATCH_THREAD: threading.Thread | None = None
_WATCHER_ERROR: str | None = None

DRAIN_TIMEOUT_S = float(os.getenv("HMR_RAY_SERVE_DRAIN_TIMEOUT_S", "30"))
DRAIN_POLL_S = 0.05


def event(kind: str, **fields: Any) -> None:
    record = {"event": kind, "at": time.time(), "pid": os.getpid(), **fields}
    with _STATE_LOCK:
        _EVENTS.append(record)
    print(f"HMR_RAY_SERVE {record}", flush=True)


def install(source_root: str | None = None) -> dict[str, Any]:
    """Convert the target module and start watching. Safe to call once per replica process."""
    global _INSTALLED, _MANIFEST, _MODULE, _LOOP
    with _INSTALL_LOCK:
        if _INSTALLED:
            return state()
        root = resolve_source_root(source_root or os.getenv("HMR_RAY_SERVE_SOURCE_ROOT"))
        _MANIFEST = build_manifest(root)
        _MODULE = publish.patch_target_module()

        # The replica's main event loop, not the user code loop this call runs on: publication
        # must be serialised against request dispatch, and dispatch happens on the main loop.
        _LOOP = publish.live_actor()._replica_impl._event_loop  # noqa: SLF001

        _start_watcher()
        atexit.register(_stop_watcher)
        _INSTALLED = True
        event("installed", source_root=str(root), target=TARGET_PATH, sha256=_MANIFEST.files[0]["sha256"], origin=str(publish.module_origin(_MODULE)))
        return state()


def _start_watcher() -> None:
    global _WATCH_STOP, _WATCH_THREAD
    _WATCH_STOP = threading.Event()
    _WATCH_THREAD = threading.Thread(target=_watch, name="ray-serve-hmr-watch", daemon=True)
    _WATCH_THREAD.start()


def _stop_watcher() -> None:
    _WATCH_STOP.set()
    thread = _WATCH_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


def _watch() -> None:
    global _WATCHER_ERROR
    assert _MANIFEST is not None
    watched = _MANIFEST.path_for(TARGET_PATH)
    try:
        for changes in watch(str(watched), debounce=int(os.getenv("HMR_RAY_SERVE_DEBOUNCE_MS", "200")), step=25, stop_event=_WATCH_STOP):
            for change, raw in changes:
                if change is Change.deleted or Path(raw).resolve() != watched.resolve():
                    continue
                with _STATE_LOCK:
                    _PENDING[watched] = {"path": TARGET_PATH, "seen_at": time.monotonic()}
                event("source_change", path=TARGET_PATH, change=change.name)
            _schedule_publish()
    except Exception as exc:
        _WATCHER_ERROR = f"{type(exc).__name__}: {exc}"
        event("watcher_failed", error=_WATCHER_ERROR)


def _schedule_publish() -> None:
    """Hand publication to the replica's main loop; the watcher thread must not do it itself."""
    loop = _LOOP
    if loop is None or loop.is_closed():
        event("publish_skipped", reason="no live event loop")
        return
    asyncio.run_coroutine_threadsafe(_publish_at_request_boundary(), loop)


def syntax_preflight(path: Path) -> tuple[bool, str | None]:
    """Never hand a half-written file to the loader: its `SyntaxError` handling is unrecoverable.

    `compile()`, not `ast.parse()`: the loader compiles, and `compile` rejects a strict
    superset of what `ast.parse` rejects. Module-level `return`/`await`/`break`/`continue`,
    a duplicate argument name and a module-level `nonlocal` all build a valid AST and only
    fail when compiled, so an `ast.parse` guard passes them to `ReactiveModule.__load`,
    whose `compile` raises `SyntaxError` into `sys.excepthook` and returns normally --
    leaving the namespace on the old code while this publication reports success.
    """
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True)
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


async def _wait_for_quiesce() -> tuple[bool, int]:
    """Wait until no request is in flight in this replica, so nothing spans the swap."""
    replica_impl = publish.live_actor()._replica_impl  # noqa: SLF001
    deadline = time.monotonic() + DRAIN_TIMEOUT_S
    ongoing = replica_impl.get_num_ongoing_requests()
    if ongoing:
        event("deferred", ongoing_requests=ongoing)
    while ongoing and time.monotonic() < deadline:
        await asyncio.sleep(DRAIN_POLL_S)
        ongoing = replica_impl.get_num_ongoing_requests()
    return ongoing == 0, ongoing


async def _publish_at_request_boundary() -> dict[str, Any]:
    """Reload and rebind, on the main loop, with nothing in flight."""
    with _PUBLISH_LOCK:
        with _STATE_LOCK:
            items = list(_PENDING.items())
        if not items:
            return {"published": False, "reason": "nothing pending"}
        assert _MANIFEST is not None and _MODULE is not None

        path = _MANIFEST.path_for(TARGET_PATH)
        ok, error = syntax_preflight(path)
        if not ok:
            # Stays pending: the finished write, or a fix, republishes. The live instances are
            # untouched, so the old implementation is still serving.
            event("rejected", error=error, retryable=True, phase="syntax_preflight")
            return {"published": False, "reason": error}

        origin = publish.module_origin(_MODULE)
        if origin != path.resolve():
            with _STATE_LOCK:
                _PENDING.clear()
            event("rejected", error=f"live module origin {origin} is not the watched file {path}", retryable=False, phase="origin_check")
            return {"published": False, "reason": "origin mismatch"}

        quiesced, ongoing = await _wait_for_quiesce()
        if not quiesced:
            event("rejected", error=f"{ongoing} request(s) still in flight after {DRAIN_TIMEOUT_S}s", retryable=True, phase="quiesce")
            return {"published": False, "reason": "did not quiesce"}

        digest = _MANIFEST.files[0]["sha256"]
        with _STATE_LOCK:
            _PENDING.clear()
        try:
            publish.reload_target(_MODULE)
        except Exception as exc:
            # The loader holds a failed module, but the live instances still point at the old
            # classes, so the replica keeps serving old code. Re-queue for a fixed file.
            with _STATE_LOCK:
                _PENDING[path] = {"path": TARGET_PATH, "seen_at": time.monotonic()}
            event("rejected", error=f"{type(exc).__name__}: {exc}", retryable=True, phase="reload")
            return {"published": False, "reason": "reload raised"}

        rebound = publish.rebind_live_instances(_MODULE)
        from .scope import sha256

        event("published", **rebound, sha256_before=digest, sha256_after=sha256(path))
        return {"published": True, **rebound}


def publish_now() -> dict[str, Any]:
    """Force a publication attempt from outside the loop (used by the example's readback)."""
    loop = _LOOP
    if loop is None or loop.is_closed():
        return {"published": False, "reason": "no live event loop"}
    return asyncio.run_coroutine_threadsafe(_publish_at_request_boundary(), loop).result(timeout=DRAIN_TIMEOUT_S + 10)


def state() -> dict[str, Any]:
    with _STATE_LOCK:
        pending = list(_PENDING.values())
        events = list(_EVENTS)
    thread = _WATCH_THREAD
    watching = thread is not None and thread.is_alive()
    origin = publish.module_origin(_MODULE) if _MODULE is not None else None
    return {
        "installed": _INSTALLED,
        "watching": watching,
        "watcher_error": _WATCHER_ERROR,
        # `installed` alone would still read True after the watcher thread died, i.e. a replica
        # that can no longer see any edit would look healthy. Assert on `healthy`.
        "healthy": _INSTALLED and watching and _WATCHER_ERROR is None,
        "pid": os.getpid(),
        "pending": pending,
        "origin": str(origin) if origin else None,
        "manifest": _MANIFEST.as_dict() if _MANIFEST else None,
        "events": events,
    }
