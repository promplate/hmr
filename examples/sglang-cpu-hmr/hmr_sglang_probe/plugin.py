"""Read-only identity readback, attached through SGLang's official hook registry.

`Scheduler.get_internal_state` already round-trips a dict to `GET /server_info`, so an
AFTER hook on it is enough to report model/module identity from inside the scheduler
process without touching a single SGLang source file. This is the setup-only
observability instrumentation, installed before the baseline request — it is not the
post-baseline one-file mutation under test.
"""

# SGLang is a runtime peer of this example, not a build/type dependency.
# pyright: reportMissingImports=false

from __future__ import annotations

import os
import sys
from typing import Any

TRACKED_MODULES = ("sglang.srt.model_executor.forward_context", "sglang.srt.model_executor.model_runner", "sglang.srt.managers.scheduler")


def _module_identity() -> dict[str, dict[str, Any]]:
    """`id()` of the live function objects, which is what an in-place swap changes.

    Read out of `sys.modules`, not through an import: a reactive module that this
    process never imported must stay absent from the report rather than be pulled in
    by the probe itself.
    """
    rows: dict[str, dict[str, Any]] = {}
    for name in TRACKED_MODULES:
        module = sys.modules.get(name)
        if module is None:
            continue
        rows[name] = {
            "module_file": getattr(module, "__file__", None),
            "symbols": {symbol: id(getattr(module, symbol)) for symbol in ("has_forward_context", "get_forward_context", "ModelRunner", "Scheduler") if hasattr(module, symbol)},
        }
    return rows


def _hmr_state() -> dict[str, Any]:
    from sglang_hmr.runtime.bootstrap import state

    return state()


def readback(result, self, *_args, **_kwargs):
    """AFTER hook on `Scheduler.get_internal_state`.

    `result` is a `GetInternalStateReqOutput` whose `internal_state` dict reaches
    `/server_info` verbatim. Returning None would keep the original result, so the
    dict is mutated in place and handed back.
    """
    try:
        runner = self.tp_worker.model_runner
        model = runner.model
        parameter = next(model.parameters(), None)
        result.internal_state["hmr_probe"] = {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "model_runner_id": id(runner),
            "model_id": id(model),
            "model_class_id": id(type(model)),
            "model_class": f"{type(model).__module__}.{type(model).__qualname__}",
            # `data_ptr()` is the allocation itself: a weight reload would move it.
            "first_parameter_data_ptr": None if parameter is None else parameter.data_ptr(),
            "weight_load_time": getattr(runner, "weight_load_time", None),
            "modules": _module_identity(),
            "hmr": _hmr_state(),
        }
    except Exception as exc:  # a probe that raises would take `/server_info` down with it
        result.internal_state["hmr_probe_error"] = f"{type(exc).__name__}: {exc}"
    return result


def register_hooks() -> None:
    """The `sglang.srt.plugins` entry point. Registration only; SGLang applies the hook."""
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    HookRegistry.register("sglang.srt.managers.scheduler.Scheduler.get_internal_state", readback, HookType.AFTER)
