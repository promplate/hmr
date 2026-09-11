"""Worker-side identity and publication RPCs for the CPU smoke."""

# ruff: noqa: FBT001, FBT002

# pyright: reportMissingImports=false, reportAttributeAccessIssue=false

from __future__ import annotations

import os
from typing import Any


class HMRWorkerExtension:
    """Mixed into vLLM's CPU worker for two disposable RPCs."""

    def hmr_probe_sync_pending(self, force: bool = False) -> dict[str, Any]:
        from .bootstrap import sync_pending

        return sync_pending(force=force)

    def hmr_probe_state(self) -> dict[str, Any]:
        import torch

        from .bootstrap import state
        from .telemetry import snapshot

        model = self.get_model()
        params = []
        for index, (name, value) in enumerate(model.named_parameters()):
            if index == 16:
                break
            params.append(
                {
                    "name": name,
                    "data_ptr": value.data_ptr(),
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                }
            )
        if torch.cuda.is_available():
            memory = {
                "kind": "cuda",
                "allocated": torch.cuda.memory_allocated(),
                "reserved": torch.cuda.memory_reserved(),
            }
        else:
            memory = {
                "kind": "cpu",
                "rss_bytes": __import__("psutil").Process().memory_info().rss,
            }
        return {
            "pid": os.getpid(),
            "rank": getattr(self, "rank", None),
            "device": str(getattr(self, "device", None)),
            "model_id": id(model),
            "model_class_id": id(type(model)),
            "model_class": f"{type(model).__module__}.{type(model).__qualname__}",
            "parameter_sample": params,
            "accelerator_memory": memory,
            "hmr": state(),
            "telemetry": snapshot(),
        }
