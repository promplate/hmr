"""Opt-in earliest-possible HMR injection for every spawned interpreter."""

import os
from pathlib import Path


def _is_model_registry_inspector() -> bool:
    """Do not leave a watchfiles thread in vLLM's short-lived probe process."""
    try:
        command = Path("/proc/self/cmdline").read_bytes()
    except OSError:
        return False
    return b"vllm.model_executor.models.registry" in command


if os.getenv("HMR_VLLM_ENABLE") == "1" and not _is_model_registry_inspector():
    from hmr_vllm_probe.bootstrap import install_from_env

    install_from_env()
