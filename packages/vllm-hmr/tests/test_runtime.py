"""Functional test for the packaged runtime: a change must actually reach the live callable.

This runs on a synthetic tree that mirrors the verified vLLM import shape (a provider
module plus a direct `from ... import` consumer) rather than on real vLLM, so it can run
anywhere. It proves the publication mechanism, not vLLM integration; the vLLM evidence
is `examples/vllm-cpu-hmr`.

The runtime installs a process-wide import hook and a watcher thread, so it can only be
installed once per interpreter. Everything here shares one subprocess-free module state,
which is why the whole scenario is a single test.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vllm_hmr.runtime import scope

try:
    import reactivity.hmr.core  # noqa: F401
    import watchfiles  # noqa: F401
except ImportError:  # pragma: no cover - exercised only in a stripped environment
    RUNTIME_DEPS = False
else:
    RUNTIME_DEPS = True

PROVIDER = "def extract_prompt_components():\n    return {marker!r}\n"
CONSUMER = "from vllm.renderers.inputs.preprocess import extract_prompt_components\n\n\ndef add_request():\n    return extract_prompt_components()\n"


@unittest.skipUnless(RUNTIME_DEPS, "requires `hmr` and `watchfiles`")
class RuntimePublicationTests(unittest.TestCase):
    def test_a_source_edit_reaches_the_live_callable_through_its_dependent(self):
        from vllm_hmr.runtime import bootstrap

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for relative in ("vllm", "vllm/renderers", "vllm/renderers/inputs", "vllm/v1", "vllm/v1/engine"):
            (root / relative).mkdir(parents=True, exist_ok=True)
            (root / relative / "__init__.py").write_text("", encoding="utf-8")
        provider = root / scope.TARGET
        provider.write_text(PROVIDER.format(marker="V1"), encoding="utf-8")
        (root / scope.DEPENDENT_PATH).write_text(CONSUMER, encoding="utf-8")

        original_path = list(sys.path)
        sys.path.insert(0, str(root))
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        for name in [name for name in sys.modules if name == "vllm" or name.startswith("vllm.")]:
            del sys.modules[name]
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in [n for n in sys.modules if n == "vllm" or n.startswith("vllm.")]])

        self.set_env("HMR_VLLM_SOURCE_ROOT", str(root))
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001 - the watcher thread is module-private but must not outlive the test
        state = bootstrap.state()
        self.assertTrue(state["installed"])
        self.assertEqual(state["manifest"]["source_root"], str(root))
        self.assertEqual(state["manifest"]["reactive_paths"], list(scope.REACTIVE_PATHS))

        consumer = importlib.import_module(scope.DEPENDENT)
        self.assertEqual(consumer.add_request(), "V1")

        provider.write_text(PROVIDER.format(marker="V2"), encoding="utf-8")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not bootstrap.state()["pending"]:
            time.sleep(0.05)
        self.assertEqual([item["path"] for item in bootstrap.state()["pending"]], [scope.TARGET])

        result = bootstrap.sync_pending()
        self.assertEqual(result["rejected"], [])
        self.assertEqual([item["path"] for item in result["published"]], [scope.TARGET])
        self.assertEqual(result["published"][0]["forced_dependents_reexecuted"], [scope.DEPENDENT])
        self.assertEqual(importlib.import_module(scope.DEPENDENT).add_request(), "V2")

        self.assertEqual(bootstrap.sync_pending()["published"], [])  # nothing queued, nothing republished

        # An in-scope file that exists and parses but that the live process never imported
        # is a decoy source root, not a publication: `vllm` came from somewhere else, so
        # reporting it as published would claim a swap of code that is not running.
        # `source.py` cannot rule this out from the outside, so publication must.
        decoy = Path(tmp.name) / "decoy" / scope.TARGET
        decoy.parent.mkdir(parents=True)
        decoy.write_text(PROVIDER.format(marker="V3"), encoding="utf-8")
        with bootstrap._STATE_LOCK:  # noqa: SLF001 - the queue is module-private; this stands in for a watcher event
            bootstrap._PENDING[decoy] = {"path": scope.TARGET, "seen_at": time.monotonic()}  # noqa: SLF001
        result = bootstrap.sync_pending()
        self.assertEqual(result["published"], [])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("not loaded from this source root", result["rejected"][0]["error"])
        self.assertEqual(importlib.import_module(scope.DEPENDENT).add_request(), "V2")  # the live callable is untouched

        # A fork inherits `_INSTALLED` but neither the watcher thread nor a runnable lock,
        # so the child must rebuild both or it reports HMR as installed while nothing is
        # ever queued. This is what `os.register_at_fork` runs in the child.
        from vllm_hmr.runtime import telemetry

        bootstrap._stop_watcher()  # noqa: SLF001 - the parent's thread does not exist in a real child
        state_lock, telemetry_lock, before = bootstrap._STATE_LOCK, telemetry._LOCK, bootstrap._WATCH_THREAD  # noqa: SLF001
        bootstrap._after_fork()  # noqa: SLF001
        watcher = bootstrap._WATCH_THREAD  # noqa: SLF001
        self.assertIsNotNone(watcher)
        assert watcher is not None
        self.assertIsNot(watcher, before)
        self.assertTrue(watcher.is_alive())
        self.assertIsNot(bootstrap._STATE_LOCK, state_lock)  # noqa: SLF001
        self.assertIsNot(telemetry._LOCK, telemetry_lock)  # noqa: SLF001
        self.assertEqual(telemetry.active_scopes(), 0)

    def set_env(self, key: str, value: str) -> None:
        import os

        original = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.__setitem__(key, original) if original is not None else os.environ.pop(key, None))


if __name__ == "__main__":
    unittest.main()
