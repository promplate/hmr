"""Evidence probe, delivered as the `sitecustomize` that `xinference-hmr`'s shim chains to.

`xinference-hmr` prepends its own shim directory to `PYTHONPATH`; this directory sits
just after it, so `chain_to_next_sitecustomize` executes this file before HMR installs.
That keeps all injection in one mechanism and exercises the chaining, rather than adding
a second one only the smoke uses.

Nothing here belongs in the package: this records identity so the smoke can *check* that
HMR did not replace the actor, the model object, its class, or its weights. The package
must not ship the assertions that verify the package.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

RECORD_PATH = Path(os.getenv("HMR_XINFERENCE_PROBE_RECORD", "/probe/identity.jsonl"))
TARGET_MODULE = "xinference.model.llm.transformers.utils"
TARGET_FUNCTION = "batch_inference_one_step"

_LOCK = threading.Lock()
_LOAD_CALLS = 0


def _record(kind: str, **fields) -> None:
    line = json.dumps({"kind": kind, "pid": os.getpid(), "t": time.time(), "mono": time.monotonic(), **fields}, default=repr)
    with _LOCK:
        try:
            RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
            with RECORD_PATH.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError as exc:  # a probe that cannot write must be loud, not silently absent
            print(f"HMR_XINFERENCE_PROBE_WRITE_FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _target_identity() -> dict:
    """Identity of the module and function the mutation targets, as this process sees them now."""
    module = sys.modules.get(TARGET_MODULE)
    if module is None:
        return {"module_loaded": False}
    out: dict = {
        "module_loaded": True,
        "module_id": id(module),
        "module_type": type(module).__name__,
        "module_file": getattr(module, "__file__", None),
    }
    function = getattr(module, TARGET_FUNCTION, None)
    if function is not None:
        code = getattr(function, "__code__", None)
        out |= {
            "function_id": id(function),
            "code_id": id(code) if code is not None else None,
            "co_filename": getattr(code, "co_filename", None),
            "co_firstlineno": getattr(code, "co_firstlineno", None),
            # The marker is inserted as a literal, so a re-executed module carries it in co_consts.
            "co_consts_markers": [const for const in getattr(code, "co_consts", ()) if isinstance(const, str) and const.startswith("HMR_PROBE_XINFERENCE_")],
        }
    return out


def _hmr_state() -> dict:
    """The package's own account of itself, read from inside the actor process.

    Read through the installed package rather than through a REST endpoint on purpose: the
    claim under test is about *this* process, and the API process would report its own state.
    """
    try:
        from xinference_hmr.runtime.bootstrap import state

        return state()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _model_identity(actor) -> dict:
    """Identity of the actor, the Xinference model wrapper, its class, and the loaded weights.

    Read defensively: this runs on the request path, and a probe that raises would turn a
    successful inference into a 500 and destroy the very evidence it exists to collect.
    """
    out: dict = {"actor_id": id(actor), "actor_repr": repr(actor), "actor_class_id": id(type(actor))}
    try:
        model = actor._model  # noqa: SLF001 - reading Xinference internals is this probe's whole purpose
    except Exception as exc:
        return out | {"model_error": f"{type(exc).__name__}: {exc}"}
    out |= {
        "model_id": id(model),
        "model_class": type(model).__name__,
        "model_class_id": id(type(model)),
        "model_class_module": type(model).__module__,
        "model_class_module_file": getattr(sys.modules.get(type(model).__module__), "__file__", None),
    }
    inner = getattr(model, "_model", None)
    if inner is not None:
        out |= {"torch_module_id": id(inner), "torch_module_class": type(inner).__name__}
        try:
            params = list(inner.parameters())
            out |= {
                "param_count": len(params),
                # Storage pointers, not a checksum: these prove the weights were not reallocated,
                # which is what "no reload" means here. In-place writes would keep them.
                "param_ptrs_head": [int(p.data_ptr()) for p in params[:5]],
                "param_dtypes_head": [str(p.dtype) for p in params[:5]],
            }
        except Exception as exc:
            out |= {"param_error": f"{type(exc).__name__}: {exc}"}
    tokenizer = getattr(model, "_tokenizer", None)
    if tokenizer is not None:
        out |= {"tokenizer_id": id(tokenizer), "tokenizer_class": type(tokenizer).__name__}
    scheduler = getattr(model, "_batch_scheduler", None)
    out |= {"batch_scheduler_id": id(scheduler) if scheduler is not None else None}
    return out


def _install() -> None:
    from xinference.core.model import ModelActor

    if getattr(ModelActor, "_hmr_probe_installed", False):
        return

    original_load = ModelActor.load
    original_generate = ModelActor.generate
    original_chat = ModelActor.chat

    async def load(self, *args, **kwargs):
        global _LOAD_CALLS
        with _LOCK:
            _LOAD_CALLS += 1
            attempt = _LOAD_CALLS
        # Recorded before *and* after: "no second load" needs the count to be visible even if a
        # later load raises partway through, which is a different failure from never being called.
        _record("load_enter", attempt=attempt)
        result = await original_load(self, *args, **kwargs)
        with _LOCK:
            calls = _LOAD_CALLS
        _record("load_exit", attempt=attempt, load_calls_at_exit=calls, **_model_identity(self), **_target_identity())
        return result

    def _wrap_request(original, name):
        async def wrapper(self, *args, **kwargs):
            # After the package's boundary, which lives in `__on_receive__`: by the time this runs,
            # any publication for this request has already happened, so the identity recorded here
            # is the post-publication state the request will actually execute against.
            with _LOCK:
                loads = _LOAD_CALLS
            hmr_state = _hmr_state()
            _record(
                "request",
                method=name,
                load_calls=loads,
                hmr_state=hmr_state,
                # Flattened out of the state so the smoke can scan every record's events in one
                # pass; the runtime's telemetry is a bounded deque, so a later record may have
                # dropped an event an earlier one still carries.
                hmr_events=(hmr_state.get("telemetry") or {}).get("events") or [],
                **_model_identity(self),
                **_target_identity(),
            )
            return await original(self, *args, **kwargs)

        return wrapper

    ModelActor.load = load
    ModelActor.generate = _wrap_request(original_generate, "generate")
    ModelActor.chat = _wrap_request(original_chat, "chat")
    ModelActor._hmr_probe_installed = True  # noqa: SLF001 - process-local idempotence marker on the patched upstream class
    _record("probe_installed", orig_argv=sys.orig_argv)


class _PostImportHook:
    """Run `_install` right after `xinference.core.model` finishes executing.

    Same shape as the package's own hook, and for the same reason: importing
    `xinference.core.model` at `site` time would pull torch into every process in the tree.
    """

    def __init__(self, module_name: str, callback):
        self.module_name = module_name
        self.callback = callback
        self._finding = False

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002 - MetaPathFinder signature
        if fullname != self.module_name or self._finding:
            return None
        self._finding = True
        try:
            from importlib.util import find_spec

            spec = find_spec(fullname)
        except (ImportError, AttributeError, ValueError):
            return None
        finally:
            self._finding = False
        if spec is None or spec.loader is None:
            return None
        callback, loader = self.callback, spec.loader
        original_exec = loader.exec_module

        def exec_module(module):
            original_exec(module)
            callback()

        loader.exec_module = exec_module
        return spec


# Gated on the same role check the package uses, so the probe records the model-owning
# sub-pool and nothing else. Without this, the REST and supervisor processes would each
# append their own "module_loaded: false" rows and dilute the evidence file.
from xinference_hmr.runtime import is_model_pool_process  # noqa: E402 - intentionally late: only the model sub-pool may import the runtime

if is_model_pool_process():
    sys.meta_path.insert(0, _PostImportHook("xinference.core.model", _install))
