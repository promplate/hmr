"""Process-local telemetry: just enough to explain what happened.

No active-scope counting for SGLang: unlike vLLM's middleware, we have no request
boundary hook, so publication happens from a watcher thread without in-flight guards.
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


def event(kind: str, **fields: Any) -> None:
    with _LOCK:
        _EVENTS.append({"t": time.monotonic(), "kind": kind, **fields})


def reset_after_fork() -> None:
    """A lock held by another thread at fork time is locked forever in the child."""
    global _LOCK
    _LOCK = threading.RLock()


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {"pid": os.getpid(), "uptime_s": time.monotonic() - _STARTED, "events": list(_EVENTS), "source_root": os.getenv("HMR_SGLANG_SOURCE_ROOT"), "orig_argv": sys.orig_argv}
