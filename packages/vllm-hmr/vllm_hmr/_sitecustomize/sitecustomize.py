"""Earliest-possible HMR injection point, activated only by explicit environment.

`vllm-hmr` prepends this directory to `PYTHONPATH`, which makes CPython import
this file during `site` initialization — before vLLM, torch, or any user module.
Because this shadows any other `sitecustomize` further down the path, the first
thing we do is chain to that one, so an environment that already relies on its
own `sitecustomize` keeps working.

Nothing is injected unless `HMR_VLLM_ENABLE=1` and `HMR_VLLM_RUNTIME` are both
set, so a plain `vllm-hmr serve ...` remains a bare vLLM launch.
"""

from vllm_hmr.shim import chain_to_next_sitecustomize, install_from_env

chain_to_next_sitecustomize(__file__)
install_from_env()
