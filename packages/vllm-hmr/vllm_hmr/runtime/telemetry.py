"""Process-local telemetry: just enough to defer publication and explain what happened.

The in-flight HTTP scope count is not diagnostics, it is the safety interlock: a
source swap while a request is mid-flight would let one request observe two
different versions of the same module.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from typing import Any

_LOCK = threading.RLock()
_STARTED = time.monotonic()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=200)
_ACTIVE_SCOPES = 0


def event(kind: str, **fields: Any) -> None:
    with _LOCK:
        _EVENTS.append({"t": time.monotonic(), "kind": kind, **fields})


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
    global _LOCK, _ACTIVE_SCOPES
    _LOCK = threading.RLock()
    _ACTIVE_SCOPES = 0  # no HTTP request survives a fork, so nothing is in flight here


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "pid": os.getpid(),
            "uptime_s": time.monotonic() - _STARTED,
            "active_scopes": _ACTIVE_SCOPES,
            "events": list(_EVENTS),
            "source_root": os.getenv("HMR_VLLM_SOURCE_ROOT"),
            "orig_argv": sys.orig_argv,
        }
