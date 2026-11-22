"""Importable, testable half of the `sitecustomize` shim used by `vllm-hmr`."""

from __future__ import annotations

import os
import sys
from importlib import import_module
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path

SKIP_MARKER = "HMR_VLLM_SKIP"  # set by a process that must not inherit injection (short-lived vLLM probes)


def parse_runtime(spec: str) -> tuple[str, str]:
    """Parse `module:callable`. Raising here is deliberate: a typo must not silently disable HMR."""
    module, sep, attribute = spec.partition(":")
    if not sep or not module or not attribute:
        raise ValueError(f"HMR_VLLM_RUNTIME must be 'module:callable', got {spec!r}")
    return module, attribute


def should_install(env: dict[str, str]) -> bool:
    return env.get("HMR_VLLM_ENABLE") == "1" and not env.get(SKIP_MARKER) and bool(env.get("HMR_VLLM_RUNTIME"))


def load_runtime(spec: str):
    module, attribute = parse_runtime(spec)
    target = getattr(import_module(module), attribute)
    if not callable(target):
        raise ValueError(f"HMR_VLLM_RUNTIME {spec!r} resolves to {type(target).__name__}, which is not callable")
    return target


def check_runtime(spec: str) -> None:
    """Resolve the spec for real, so the wrapper can reject it before `execve`.

    `site` reports a failing `sitecustomize` as one line on stderr and carries on, which inside
    vLLM's startup output means a typo'd module, a missing attribute, or a non-callable target
    reads as "HMR silently did nothing". Same resolution as `load_runtime`, minus the call.
    """
    try:
        load_runtime(spec)
    except ValueError:
        raise
    except Exception as exc:  # an ImportError from the runtime's own imports is equally fatal, and equally invisible later
        raise ValueError(f"HMR_VLLM_RUNTIME {spec!r} could not be loaded: {type(exc).__name__}: {exc}") from exc


def install_from_env(env: dict[str, str] | None = None) -> object | None:
    """Invoke the configured runtime entrypoint, or do nothing when not enabled."""
    env = dict(os.environ) if env is None else env
    if not should_install(env):
        return None
    return load_runtime(env["HMR_VLLM_RUNTIME"])()


def _next_sitecustomize_spec(shim_file: str, path: list[str] | None = None):
    """Let CPython select packages, files and namespace portions in its normal order."""
    shim_dir = Path(shim_file).resolve().parent
    search_path = [entry for entry in (sys.path if path is None else path) if Path(entry).resolve() != shim_dir]
    return PathFinder.find_spec("sitecustomize", search_path)


def _spec_path(spec) -> Path:
    location = spec.origin or next(iter(spec.submodule_search_locations or ()))
    return Path(location).resolve()


def next_sitecustomize(shim_file: str, path: list[str] | None = None) -> Path | None:
    """Find the `sitecustomize` this shim shadowed, if the environment had one."""
    if spec := _next_sitecustomize_spec(shim_file, path):
        return _spec_path(spec)
    return None


def chain_to_next_sitecustomize(shim_file: str) -> Path | None:
    """Execute the shadowed `sitecustomize` in its own namespace before we install."""
    spec = _next_sitecustomize_spec(shim_file)
    if spec is None:
        return None
    module = module_from_spec(spec)
    previous = sys.modules.get("sitecustomize")
    # Keep files and packages registered: deferred imports and attribute access must see
    # the user's module. The shim still finishes executing in its own namespace.
    sys.modules["sitecustomize"] = module
    try:
        if spec.loader is not None:
            spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop("sitecustomize", None)
        else:
            sys.modules["sitecustomize"] = previous
        raise
    return _spec_path(spec)
