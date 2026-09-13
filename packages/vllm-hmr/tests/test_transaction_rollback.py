"""Transactional rollback: target load succeeds, forced dependent fails → both namespaces roll back.

Before rollback was implemented, a target module load() that succeeded followed by a forced
dependent load() that raised would leave the target's new code published while the dependent's
namespace was half-mutated. This test verifies the rollback restores both to their pre-transaction
state, preserving module identity and keeping the pending item for retry.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from vllm_hmr.runtime import bootstrap, scope

RUNTIME_DEPS = all(importlib.util.find_spec(name) is not None for name in ("reactivity", "watchfiles"))  # the runtime imports these lazily, so a stripped environment must skip rather than error

PROVIDER_V1 = "def extract_prompt_components():\n    return 'V1'\n"
PROVIDER_V2 = "def extract_prompt_components():\n    return 'V2'\n"
CONSUMER_OK = "from vllm.renderers.inputs.preprocess import extract_prompt_components\n\n\ndef add_request():\n    return extract_prompt_components()\n"
CONSUMER_BOOM = "from vllm.renderers.inputs.preprocess import extract_prompt_components\n\nMARKER = 'leaked'\n\nraise RuntimeError('forced dependent boom')\n"


@unittest.skipUnless(RUNTIME_DEPS, "requires hmr and watchfiles")
class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rel in ("vllm", "vllm/renderers", "vllm/renderers/inputs", "vllm/v1", "vllm/v1/engine"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
            (self.root / rel / "__init__.py").write_text("", encoding="utf-8")
        self.provider = self.root / scope.TARGET
        self.consumer = self.root / scope.DEPENDENT_PATH
        # Pre-create files so build_manifest validation passes
        self.provider.write_text("# placeholder\n", encoding="utf-8")
        self.consumer.write_text("# placeholder\n", encoding="utf-8")

    def tearDown(self):
        for name in [n for n in sys.modules if n == "vllm" or n.startswith("vllm.")]:
            sys.modules.pop(name, None)
        # Remove the finder and clear ReactiveModule.instances to prevent cross-test pollution
        from reactivity.hmr.core import ReactiveModule, ReactiveModuleFinder

        sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, ReactiveModuleFinder)]
        ReactiveModule.instances.clear()
        bootstrap._INSTALLED = False  # noqa: SLF001
        bootstrap._MANIFEST = None  # noqa: SLF001
        bootstrap._PENDING.clear()  # noqa: SLF001
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def set_env(self, key: str, value: str) -> None:
        original = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.__setitem__(key, original) if original is not None else os.environ.pop(key, None))

    def test_target_succeeds_dependent_fails_both_namespaces_roll_back(self):
        """Target load() succeeds, forced dependent load() fails → rollback restores both namespaces."""
        self.provider.write_text(PROVIDER_V1, encoding="utf-8")
        self.consumer.write_text(CONSUMER_OK, encoding="utf-8")
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        provider_module = importlib.import_module("vllm.renderers.inputs.preprocess")
        consumer_module = importlib.import_module(scope.DEPENDENT)
        self.assertEqual(provider_module.extract_prompt_components(), "V1")
        self.assertEqual(consumer_module.add_request(), "V1")

        # This test drives one deterministic manual publication. Stop the real watcher before
        # writing both files, otherwise it can independently queue the dependent on fast backends.
        bootstrap._stop_watcher()  # noqa: SLF001
        with bootstrap._STATE_LOCK:  # noqa: SLF001
            bootstrap._PENDING.clear()  # noqa: SLF001

        from reactivity.hmr.core import get_path_module_map

        path_map = get_path_module_map()
        prov = path_map[self.provider.resolve()]
        cons = path_map[self.consumer.resolve()]
        prov_ns_id = id(prov.__dict__)
        cons_ns_id = id(cons.__dict__)

        # Edit provider to V2, consumer to raise on re-exec
        self.provider.write_text(PROVIDER_V2, encoding="utf-8")
        self.consumer.write_text(CONSUMER_BOOM, encoding="utf-8")
        bootstrap._PENDING[self.provider.resolve()] = {"path": scope.TARGET, "seen_at": 0.0}  # noqa: SLF001

        result = bootstrap.sync_pending()
        self.assertTrue(result["installed"])
        self.assertFalse(result["deferred"])
        self.assertEqual(len(result["published"]), 0, "nothing should be published when a forced dependent fails")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("RuntimeError", result["rejected"][0]["error"])

        # Both namespaces should have rolled back to V1
        self.assertEqual(prov.__dict__["extract_prompt_components"](), "V1", "provider namespace rolled back")
        self.assertNotIn("MARKER", cons.__dict__, "consumer namespace rolled back: leaked key deleted")
        self.assertEqual(cons.__dict__["extract_prompt_components"](), "V1", "consumer bound import rolled back")

        # Module identity preserved
        self.assertEqual(id(prov.__dict__), prov_ns_id, "provider namespace dict identity preserved")
        self.assertEqual(id(cons.__dict__), cons_ns_id, "consumer namespace dict identity preserved")
        self.assertIn("__spec__", cons.__dict__)
        self.assertIn("__loader__", cons.__dict__)
        self.assertIsNotNone(cons.__dict__["__spec__"])
        self.assertEqual(cons.__dict__["__name__"], "vllm.v1.engine.async_llm")
        self.assertIn("_ReactiveModule__load", cons.__dict__, "load handle preserved in namespace")
        self.assertIs(sys.modules["vllm.v1.engine.async_llm"], cons)

        # Load handles report dirty=False since namespace matches the (old) snapshot
        prov_load = bootstrap._load_handle(prov)  # noqa: SLF001
        cons_load = bootstrap._load_handle(cons)  # noqa: SLF001
        self.assertFalse(prov_load.dirty, "provider load not dirty after rollback")
        self.assertFalse(cons_load.dirty, "consumer load not dirty after rollback")

        # The rolled-back dependent must not re-execute the still-broken file on the next request
        # either: that read is what published the leaked namespace before the flag was settled.
        self.assertEqual(consumer_module.add_request(), "V1", "the live callable still serves the old version")
        self.assertNotIn("MARKER", cons.__dict__, "reading through the module did not re-run the broken source")

        # Same interpreter, same install: the runtime patches `sys.meta_path` once per process, so a
        # second `install_from_env` is a no-op and a fresh install cannot be tested in a new method.
        # Fixing the dependent on disk and re-queueing must publish, proving rollback left the
        # transaction retryable rather than wedged.
        self.consumer.write_text(CONSUMER_OK, encoding="utf-8")
        bootstrap._PENDING[self.provider.resolve()] = {"path": scope.TARGET, "seen_at": 1.0}  # noqa: SLF001
        retried = bootstrap.sync_pending()
        self.assertEqual(retried["rejected"], [])
        self.assertEqual([item["path"] for item in retried["published"]], [scope.TARGET], "retry after the fix publishes")
        self.assertEqual(retried["published"][0]["forced_dependents_reexecuted"], [scope.DEPENDENT])
        self.assertEqual(provider_module.extract_prompt_components(), "V2", "now on V2 after the successful retry")
        self.assertEqual(consumer_module.add_request(), "V2", "the live callable reaches the new version through its dependent")


if __name__ == "__main__":
    unittest.main()
