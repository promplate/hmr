"""Observability-only SGLang plugin for the CPU HMR smoke.

This holds no HMR state: it installs no watcher, publishes nothing, and makes no
reload decision. Every HMR decision in the receipt is made by `sglang_hmr`.

It is loaded through SGLang's own `sglang.srt.plugins` entry-point group, so the
smoke does not have to edit any SGLang source file to read identity back. SGLang
calls `load_plugins()` in every process that matters here — the listener
(`sglang/launch_server.py:71`, `sglang/cli/serve.py:189`) and each scheduler child
(`sglang/srt/managers/scheduler.py:5770`, before `Scheduler` is constructed).
"""

from .plugin import register_hooks

__all__ = ["register_hooks"]
