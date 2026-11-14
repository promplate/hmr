"""Worker-side publication RPC.

Mixed into vLLM's worker by the `vllm-hmr` CLI as
`--worker-extension-cls vllm_hmr.runtime.worker.HMRWorkerExtension`. The API process
cannot reload modules inside a worker process, so the worker must do it itself when
told to, at the same request boundary.
"""

from __future__ import annotations

# ruff: noqa: FBT001, FBT002
from typing import Any


class HMRWorkerExtension:
    """Only ever reachable inside a worker process, via `collective_rpc`.

    vLLM injects this class into the resolved worker class's bases in `init_worker`, so
    `self` is the real worker and `rank` is `WorkerBase.rank`. Nothing here touches the
    model, its parameters, or the device: none of that is in this package's verified
    scope, and reporting it would imply a guarantee the evidence does not cover.
    """

    rank: int  # provided by the WorkerBase this class is mixed into, never by this class

    def vllm_hmr_sync_pending(self, force: bool = False) -> dict[str, Any]:
        from .bootstrap import sync_pending

        return sync_pending(force=force)

    def vllm_hmr_state(self) -> dict[str, Any]:
        import os

        from .bootstrap import state

        return {"pid": os.getpid(), "rank": self.rank, "hmr": state()}
