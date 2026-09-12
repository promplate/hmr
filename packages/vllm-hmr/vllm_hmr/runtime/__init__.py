"""Default narrow-scope HMR runtime for vLLM CPU 0.28.0 verified request path.

This runtime only supports the single verified provider + direct dependent:
  - vllm/renderers/inputs/preprocess.py
  - vllm/v1/engine/async_llm.py (direct from-import consumer)

Any source/manifest mismatch fails fast. This is not a whole-vLLM watcher.
"""
