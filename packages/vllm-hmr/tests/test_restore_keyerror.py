"""Rollback of a name the failed load created without going through the namespace proxy.

`exec(code, self.__namespace, self.__namespace_proxy)` gives a module two mappings: globals is the
raw dict, locals is the proxy. Module-level statements compile to `STORE_NAME`, which writes the
proxy and keeps its per-name signal in step. A `global` write inside a function compiles to
`STORE_GLOBAL`, which writes the raw dict directly — the proxy never sees it, so `_keys[name]`
stays `False` while the name is present in `module.__dict__`.

Rollback has to delete exactly those names, and `ReactiveMappingProxy.__delitem__` raises
`KeyError` when the signal reads `False`. So the delete of a `STORE_GLOBAL` name raises out of
`sync_pending`, which is a 500 at the request boundary and a namespace left holding the failed
load's leftovers.
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

RUNTIME_DEPS = all(importlib.util.find_spec(name) is not None for name in ("reactivity", "watchfiles"))

PROVIDER_V1 = "def extract_prompt_components():\n    return 'V1'\n"
# `global` inside a function: STORE_GLOBAL writes the raw namespace dict, bypassing the proxy, so
# the name exists with a `False` signal. Raising afterwards is what sends this through rollback.
PROVIDER_STORE_GLOBAL_BOOM = """def _leak():
    global LEAKED_VIA_STORE_GLOBAL
    LEAKED_VIA_STORE_GLOBAL = "bypassed the proxy"


_leak()

raise RuntimeError("boom after STORE_GLOBAL")
"""
CONSUMER = "from vllm.renderers.inputs.preprocess import extract_prompt_components\n\n\ndef add_request():\n    return extract_prompt_components()\n"


@unittest.skipUnless(RUNTIME_DEPS, "requires hmr and watchfiles")
class RestoreStoreGlobalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rel in ("vllm", "vllm/renderers", "vllm/renderers/inputs", "vllm/v1", "vllm/v1/engine"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
            (self.root / rel / "__init__.py").write_text("", encoding="utf-8")
        self.provider = self.root / scope.TARGET
        self.consumer = self.root / scope.DEPENDENT_PATH
        self.provider.write_text(PROVIDER_V1, encoding="utf-8")
        self.consumer.write_text(CONSUMER, encoding="utf-8")

    def tearDown(self):
        for name in [n for n in sys.modules if n == "vllm" or n.startswith("vllm.")]:
            sys.modules.pop(name, None)
        from reactivity.hmr.core import ReactiveModule, ReactiveModuleFinder

        sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, ReactiveModuleFinder)]
        ReactiveModule.instances.clear()
        bootstrap._INSTALLED = False  # noqa: SLF001
        bootstrap._MANIFEST = None  # noqa: SLF001
        bootstrap._PENDING.clear()  # noqa: SLF001
        bootstrap._FILE_DIGESTS.clear()  # noqa: SLF001
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def set_env(self, key: str, value: str) -> None:
        original = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.__setitem__(key, original) if original is not None else os.environ.pop(key, None))

    def test_rollback_deletes_a_store_global_name_without_raising(self):
        """A failed load's `STORE_GLOBAL` name is removed, and the proxy agrees it is gone."""
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        provider_module = importlib.import_module(scope.TARGET.removesuffix(".py").replace("/", "."))
        consumer_module = importlib.import_module(scope.DEPENDENT)
        self.assertEqual(provider_module.extract_prompt_components(), "V1")
        self.assertEqual(consumer_module.add_request(), "V1")

        from reactivity.hmr.core import get_path_module_map

        module = get_path_module_map()[self.provider.resolve()]
        namespace_id = id(module.__dict__)

        self.provider.write_text(PROVIDER_STORE_GLOBAL_BOOM, encoding="utf-8")
        bootstrap._PENDING[self.provider.resolve()] = {"path": scope.TARGET, "seen_at": 0.0}  # noqa: SLF001

        # The bug surfaces as `KeyError` escaping this call, not as a rejection.
        result = bootstrap.sync_pending()

        self.assertEqual(result["published"], [], "a load that raised must not be published")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("RuntimeError", result["rejected"][0]["error"], "the module's own error, not a rollback KeyError")

        self.assertNotIn("LEAKED_VIA_STORE_GLOBAL", module.__dict__, "the raw dict no longer holds the failed load's name")
        proxy = module._ReactiveModule__namespace_proxy  # noqa: SLF001
        self.assertNotIn("LEAKED_VIA_STORE_GLOBAL", set(proxy), "the proxy agrees the name is gone")
        with self.assertRaises(AttributeError):
            _ = module.LEAKED_VIA_STORE_GLOBAL  # attribute access must not resurrect it either

        self.assertEqual(id(module.__dict__), namespace_id, "namespace dict identity preserved")
        self.assertEqual(provider_module.extract_prompt_components(), "V1", "rolled back to the working version")
        self.assertEqual(consumer_module.add_request(), "V1", "the consumer still reaches the old function object")
        self.assertFalse(bootstrap._load_handle(module).dirty, "a rolled-back module must not re-execute the broken file")  # noqa: SLF001

    def test_rollback_restores_a_name_the_failed_load_rebound_via_store_global(self):
        """`STORE_GLOBAL` over an existing name desyncs nothing, but the old value must come back."""
        self.provider.write_text("MARKER = 'before'\n\n\ndef extract_prompt_components():\n    return MARKER\n", encoding="utf-8")
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        provider_module = importlib.import_module(scope.TARGET.removesuffix(".py").replace("/", "."))
        self.assertEqual(provider_module.extract_prompt_components(), "before")

        from reactivity.hmr.core import get_path_module_map

        module = get_path_module_map()[self.provider.resolve()]

        self.provider.write_text(
            'MARKER = "before"\n\n\ndef _clobber():\n    global MARKER\n    MARKER = "after"\n\n\n_clobber()\n\nraise RuntimeError("boom after rebinding MARKER")\n',
            encoding="utf-8",
        )
        bootstrap._PENDING[self.provider.resolve()] = {"path": scope.TARGET, "seen_at": 0.0}  # noqa: SLF001

        result = bootstrap.sync_pending()

        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("RuntimeError", result["rejected"][0]["error"])
        self.assertEqual(module.__dict__["MARKER"], "before", "the rebound name is back to its pre-transaction value")
        self.assertEqual(provider_module.extract_prompt_components(), "before")


if __name__ == "__main__":
    unittest.main()
