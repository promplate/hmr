"""Observability for the CPU smoke: a read-only endpoint plugin and a worker subclass.

This package holds no HMR logic. Every reload decision in the smoke is made by the
installed `vllm_hmr` runtime, which is what the smoke is there to verify.
"""

__all__ = []
