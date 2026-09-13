"""Default narrow-scope HMR runtime for SGLang CPU 0.5.16 verified request path.

This runtime only supports the single verified provider + direct dependent:
  - python/sglang/srt/model_executor/forward_context.py
  - python/sglang/srt/model_executor/model_runner.py (direct from-import consumer)

Any source/manifest mismatch fails fast. This is not a whole-SGLang watcher.
"""
