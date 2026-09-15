"""Process-local telemetry: just enough to defer publication and explain what happened.

The in-flight HTTP scope count is not diagnostics, it is the safety interlock: a
source swap while a request is mid-flight would let one request observe two
different versions of the same module.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_SINK_LOCK = threading.RLock()  # separate from `_LOCK`: filesystem I/O must not delay `scope_enter`/`scope_exit`/`active_scopes`, which are the boundary's interlock
_STARTED = time.monotonic()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=200)
_ACTIVE_SCOPES = 0
_EVENT_LOG: Path | None = None
_EVENT_LOG_KEY: tuple[int, str | None] | None = None
_EVENT_LOG_ERROR: str | None = None
_SINK_DISABLED = False  # module-local, not an `os.environ` deletion: a child spawned after this failure must still get its own chance at its own filesystem


def event_log() -> Path | None:
    """This process's own append-only event file, when `HMR_VLLM_EVENT_LOG_DIR` names a directory.

    Off by default, and out-of-band on purpose. Otherwise this state is only readable over HTTP,
    and every HTTP request is a publication boundary, so anything that polls for a queued change
    is itself the boundary that publishes it. A file is observable without touching the server.

    The cache is keyed by PID and by the variable's current value, not taken once: a forked child
    inherits the parent's cache but must not append to the parent's file, and a value that changed
    must be honoured rather than fixed by whichever event happened to fire first.
    """
    global _EVENT_LOG, _EVENT_LOG_KEY
    with _SINK_LOCK:
        if _SINK_DISABLED:
            return None
        directory = os.getenv("HMR_VLLM_EVENT_LOG_DIR")
        key = (os.getpid(), directory)
        if key != _EVENT_LOG_KEY:
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                _EVENT_LOG = Path(directory) / f"hmr-events-{key[0]}.jsonl"
            else:
                _EVENT_LOG = None
            _EVENT_LOG_KEY = key
        return _EVENT_LOG


def _event_log_quietly() -> Path | None:
    """`event_log` for a reader: reporting where events go must not itself fail on an unusable directory."""
    with _SINK_LOCK:
        try:
            return event_log()
        except OSError:
            return None


def event(kind: str, **fields: Any) -> None:
    global _EVENT_LOG_ERROR, _SINK_DISABLED
    record = {"t": time.monotonic(), "kind": kind, **fields}
    with _LOCK:  # the in-memory ring only; the sink below runs under `_SINK_LOCK` so a stalled filesystem cannot hold up the boundary interlock
        _EVENTS.append(record)
    with _SINK_LOCK:
        try:
            if (path := event_log()) is not None:
                # Opened per event and closed again, rather than held: a reader must find a complete
                # file at any moment, and an inherited handle would interleave two processes' events.
                with path.open("a", encoding="utf-8") as sink:
                    sink.write(json.dumps({"pid": os.getpid(), **record}, default=repr) + "\n")
        except OSError as exc:
            # This sink is diagnostics; the callers are the watcher thread and the publication path.
            # An unwritable directory must turn the sink off and say so in the state a reader can
            # still reach, not kill the watcher and take HMR down with it. Turned off in module
            # state, not by unsetting the variable: `sitecustomize` runs before vLLM spawns its
            # engine-core and worker children, so popping it here would silence descendants whose
            # own filesystem is perfectly writable.
            _EVENT_LOG_ERROR = f"{type(exc).__name__}: {exc}"
            _SINK_DISABLED = True


def scope_enter() -> None:
    global _ACTIVE_SCOPES
    with _LOCK:
        _ACTIVE_SCOPES += 1


def scope_exit() -> None:
    global _ACTIVE_SCOPES
    with _LOCK:
        _ACTIVE_SCOPES -= 1


def active_scopes() -> int:
    with _LOCK:
        return _ACTIVE_SCOPES


def reset_after_fork() -> None:
    """A lock held by another thread at fork time is locked forever in the child."""
    global _LOCK, _SINK_LOCK, _ACTIVE_SCOPES, _EVENT_LOG, _EVENT_LOG_KEY, _EVENT_LOG_ERROR, _SINK_DISABLED
    _LOCK = threading.RLock()
    _SINK_LOCK = threading.RLock()
    _ACTIVE_SCOPES = 0  # no HTTP request survives a fork, so nothing is in flight here
    _EVENT_LOG = None  # PID changed: cache is stale even though it's PID-keyed
    _EVENT_LOG_KEY = None
    _EVENT_LOG_ERROR = None  # child gets its own chance at its own filesystem
    _SINK_DISABLED = False


def snapshot() -> dict[str, Any]:
    # Sink state read first, outside `_LOCK`: `_event_log_quietly` takes `_SINK_LOCK` and may
    # `mkdir`, and taking `_LOCK` around that would reintroduce the filesystem stall this
    # separation exists to keep away from `scope_enter`/`scope_exit`/`active_scopes`.
    path = _event_log_quietly()
    with _SINK_LOCK:
        event_log_error = _EVENT_LOG_ERROR
    with _LOCK:
        return {
            "pid": os.getpid(),
            "uptime_s": time.monotonic() - _STARTED,
            "active_scopes": _ACTIVE_SCOPES,
            "events": list(_EVENTS),
            "event_log": None if path is None else str(path),
            "event_log_error": event_log_error,
            "source_root": os.getenv("HMR_VLLM_SOURCE_ROOT"),
            "orig_argv": sys.orig_argv,
        }
