"""Fail-closed source-tree policy for the vLLM HMR experiment."""

# ruff: noqa: PIE810

from __future__ import annotations

import ast
import fnmatch
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Decision:
    stratum: str
    action: str
    reason: str

    def as_dict(self):
        return asdict(self)


CENTRAL = {
    "vllm/__init__.py",
    "vllm/envs.py",
    "vllm/config/__init__.py",
    "vllm/logger.py",
    "vllm/plugins/__init__.py",
    "vllm/platforms/__init__.py",
    "vllm/v1/engine/core.py",
    "vllm/v1/engine/core_client.py",
    "vllm/v1/worker/worker_base.py",
}


def classify(relative_path: str) -> Decision:
    p = relative_path.replace("\\", "/")
    suffix = Path(p).suffix
    if p.startswith("tests/") or p.startswith("docs/") or p.startswith("examples/") or p.startswith("tools/"):
        return Decision("test_or_docs", "no_live_effect", "outside the serving runtime")
    if p in CENTRAL:
        return Decision("central_hub", "restart_required", "global registries, process lifecycle, or cross-process protocol")
    if p.startswith("csrc/") or suffix in {".cpp", ".cc", ".c", ".cu", ".cuh", ".so"}:
        return Decision("native", "rebuild_and_worker_restart", "loaded native code cannot be transactionally overwritten")
    if suffix != ".py":
        if suffix in {".json", ".jinja", ".yaml", ".yml", ".toml"}:
            return Decision("resource", "auto_only_if_tracked_read", "pyth-on-line tracks files only when opened in a reactive computation")
        return Decision("non_runtime", "no_live_effect_or_restart", "not an automatically reloadable Python module")
    if "/kernels/" in p or "/triton" in p or "triton_" in Path(p).name:
        return Decision("kernel", "prepare_warm_switch", "new Triton object and specializations must be compiled and validated")
    if p.startswith("vllm/model_executor/models/") or p.startswith("vllm/models/"):
        return Decision("model_implementation", "restart_required", "loaded nn.Module instances retain old class and bound methods")
    if "/core/sched/" in p or "scheduler" in Path(p).name:
        return Decision("scheduler", "restart_required", "long-lived scheduler instances retain old class and mutable state")
    if p.startswith("vllm/config/") or Path(p).name.endswith("config.py"):
        return Decision("config", "restart_required", "live config objects are snapshots and schemas are process-wide")
    if "/api_router.py" in p or p.startswith("vllm/entrypoints/"):
        return Decision("route_or_entrypoint", "restart_or_stable_dispatch", "FastAPI captures route callables and app construction is one-shot")
    return Decision("python_leaf_or_utility", "candidate_preflight", "eligible only after syntax/import validation and live dependency proof")


def syntax_preflight(path: Path) -> tuple[bool, str | None]:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
