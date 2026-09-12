"""Importable, testable half of the `sitecustomize` shim used by `vllm-hmr`."""

from __future__ import annotations

import os
import sys
from importlib import import_module
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
    return getattr(import_module(module), attribute)


def install_from_env(env: dict[str, str] | None = None) -> object | None:
    """Invoke the configured runtime entrypoint, or do nothing when not enabled."""
    env = dict(os.environ) if env is None else env
    if not should_install(env):
        return None
    return load_runtime(env["HMR_VLLM_RUNTIME"])()


def next_sitecustomize(shim_file: str, path: list[str] | None = None) -> Path | None:
    """Find the `sitecustomize` this shim shadowed, if the environment had one."""
    shim_dir = Path(shim_file).resolve().parent
    for entry in sys.path if path is None else path:
        if not entry:
            continue
        candidate = Path(entry).resolve()
        if candidate == shim_dir:
            continue
        for target in (candidate / "sitecustomize.py", candidate / "sitecustomize" / "__init__.py"):
            if target.is_file():
                return target
    return None


def chain_to_next_sitecustomize(shim_file: str) -> Path | None:
    """Execute the shadowed `sitecustomize` in its own namespace before we install."""
    target = next_sitecustomize(shim_file)
    if target is None:
        return None
    code = compile(target.read_text(encoding="utf-8"), str(target), "exec")
    exec(code, {"__file__": str(target), "__name__": "sitecustomize"})
    return target
