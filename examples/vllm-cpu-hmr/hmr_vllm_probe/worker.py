"""One read-only model identity RPC, on top of the packaged worker extension.

The smoke has to prove a source swap left the loaded model untouched, which needs
`id(model)` and its parameter pointers. `vllm_hmr` deliberately reports none of
that: the model is outside its verified scope. So this subclass adds exactly one
read-only RPC and inherits the publication RPC the packaged middleware calls, which
is what keeps `--worker-extension-cls` pointing at real product code.
"""

# pyright: reportMissingImports=false

from __future__ import annotations

from itertools import islice
from typing import TYPE_CHECKING, Any

from vllm_hmr.runtime.worker import HMRWorkerExtension

if TYPE_CHECKING:
    from collections.abc import Callable


class HMRProbeWorkerExtension(HMRWorkerExtension):
    get_model: Callable[[], Any]  # provided by the WorkerBase this class is mixed into, never by this class

    def hmr_probe_identity(self) -> dict[str, Any]:
        model = self.get_model()
        return {
            **self.vllm_hmr_state(),
            "model_id": id(model),
            "model_class_id": id(type(model)),
            "model_class": f"{type(model).__module__}.{type(model).__qualname__}",
            "parameter_sample": [{"name": name, "data_ptr": value.data_ptr(), "shape": list(value.shape), "dtype": str(value.dtype)} for name, value in islice(model.named_parameters(), 16)],
        }
