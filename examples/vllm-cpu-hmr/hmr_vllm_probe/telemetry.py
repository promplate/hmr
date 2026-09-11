"""Stable, process-local telemetry for the disposable vLLM HMR probe."""

# ruff: noqa: ARG001, TC003

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, deque
from pathlib import Path
from types import FrameType
from typing import Any

_LOCK = threading.RLock()
_STARTED = time.monotonic()
_COUNTERS: Counter[str] = Counter()
_MARKERS: dict[str, str] = {}
_EVENTS: deque[dict[str, Any]] = deque(maxlen=500)
_ACTIVE_SCOPES = 0


def event(kind: str, **fields: Any) -> None:
    with _LOCK:
        _EVENTS.append({"t": time.monotonic(), "kind": kind, **fields})


def hit(key: str, marker: str | None = None) -> None:
    with _LOCK:
        _COUNTERS[key] += 1
        if marker:
            _MARKERS[key] = marker


def scope_enter(path: str) -> None:
    global _ACTIVE_SCOPES
    with _LOCK:
        _ACTIVE_SCOPES += 1
        _COUNTERS[f"http:{path}"] += 1


def scope_exit() -> None:
    global _ACTIVE_SCOPES
    with _LOCK:
        _ACTIVE_SCOPES -= 1
        if _ACTIVE_SCOPES < 0:
            raise AssertionError("negative active HMR scope count")


def active_scopes() -> int:
    with _LOCK:
        return _ACTIVE_SCOPES


def latest_marker(prefix: str = "HMR_PROBE_VLLM_") -> str | None:
    with _LOCK:
        values = [value for value in _MARKERS.values() if value.startswith(prefix)]
        return values[-1] if values else None


def _marker_from_code(frame: FrameType) -> str | None:
    for value in frame.f_code.co_consts:
        if isinstance(value, str) and value.startswith("HMR_PROBE_VLLM_"):
            return value
    return None


def profile(frame: FrameType, profile_event: str, arg: object):
    if profile_event != "call":
        return profile
    filename = frame.f_code.co_filename.replace("\\", "/")
    name = frame.f_code.co_name
    wanted = (
        (filename.endswith("vllm/renderers/inputs/preprocess.py") and name in {"<module>", "extract_prompt_components"})
        or (filename.endswith("vllm/v1/engine/async_llm.py") and name in {"<module>", "add_request"})
        or (filename.endswith("vllm/entrypoints/openai/completion/api_router.py") and name in {"<module>", "create_completion"})
        or (filename.endswith("vllm/envs.py") and name in {"<module>", "__getattr__"})
        or (filename.endswith("vllm/model_executor/models/qwen2.py") and name in {"<module>", "forward", "_hmr_probe_model_helper"})
    )
    if wanted:
        key = f"{Path(filename).as_posix()}:{name}"
        hit(key, _marker_from_code(frame))
    return profile


def install_profiler() -> None:
    import sys

    if os.getenv("HMR_VLLM_PROFILE", "1") != "1":
        return
    sys.setprofile(profile)
    threading.setprofile(profile)
    event("profile_installed")


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "process_name": __import__("multiprocessing").current_process().name,
            "uptime_s": time.monotonic() - _STARTED,
            "active_scopes": _ACTIVE_SCOPES,
            "counters": dict(_COUNTERS),
            "markers": dict(_MARKERS),
            "events": list(_EVENTS),
            "source_root": os.getenv("HMR_VLLM_SOURCE_ROOT"),
            "source_sha": os.getenv("HMR_VLLM_SOURCE_SHA"),
        }


def dump(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot(), sort_keys=True), encoding="utf-8")
    temp.replace(target)
