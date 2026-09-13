"""Watcher thread death: bounded restart with a content rescan, then fail-closed.

When the watcher thread raises (an unreadable source root, a `watchfiles` failure), it sets
`_WATCHER_FAILED` and returns, leaving the install active with nothing observing the filesystem.
Observing that state was not enough: no later edit is ever queued, so requests keep being served
by stale code while `installed` still reads `True`.

Restarting alone is not enough either. `watch()` reports changes from the moment it starts, and a
file edited during the outage is never touched again, so that edit would be missed permanently.
Recovery therefore rescans content digests to recover the gap. The budget is bounded because a
cause that is still true (a source root that went away) would otherwise be retried once per
request forever; past the budget the runtime stays fail-closed and says so.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from vllm_hmr.runtime import bootstrap

RUNTIME_DEPS = all(importlib.util.find_spec(name) is not None for name in ("reactivity", "watchfiles"))

PROVIDER_V1 = "def extract_prompt_components():\n    return 'V1'\n"
PROVIDER_V2 = "def extract_prompt_components():\n    return 'V2'\n"
CONSUMER = "from vllm.renderers.inputs.preprocess import extract_prompt_components\n\n\ndef add_request():\n    return extract_prompt_components()\n"


@unittest.skipUnless(RUNTIME_DEPS, "requires hmr and watchfiles")
class WatcherFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rel in ("vllm", "vllm/renderers", "vllm/renderers/inputs", "vllm/v1", "vllm/v1/engine"):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
            (self.root / rel / "__init__.py").write_text("", encoding="utf-8")
        self.provider = self.root / "vllm" / "renderers" / "inputs" / "preprocess.py"
        self.consumer = self.root / "vllm" / "v1" / "engine" / "async_llm.py"
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
        bootstrap._WATCHER_FAILED = False  # noqa: SLF001
        bootstrap._WATCHER_RESTARTS = 0  # noqa: SLF001 - a spent budget would make the next test's recovery unavailable
        bootstrap._FILE_DIGESTS.clear()  # noqa: SLF001 - digests of a temp dir that is about to be removed
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def set_env(self, key: str, value: str) -> None:
        original = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda: os.environ.__setitem__(key, original) if original is not None else os.environ.pop(key, None))

    def test_watcher_failure_recovers_and_rescans_on_next_sync(self):
        """A dead watcher is restarted at the next sync_pending, queuing edits made during the outage."""
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        provider_module = importlib.import_module("vllm.renderers.inputs.preprocess")
        consumer_module = importlib.import_module("vllm.v1.engine.async_llm")
        self.assertEqual(provider_module.extract_prompt_components(), "V1")
        self.assertEqual(consumer_module.add_request(), "V1")

        # Kill the watcher and mark it failed
        bootstrap._stop_watcher()  # noqa: SLF001
        with bootstrap._STATE_LOCK:  # noqa: SLF001
            bootstrap._WATCHER_FAILED = True  # noqa: SLF001

        # Edit on disk while watcher is dead — this won't be auto-queued
        self.provider.write_text(PROVIDER_V2, encoding="utf-8")

        # First sync_pending recovers the watcher and rescans, catching the missed edit
        result = bootstrap.sync_pending()
        self.assertTrue(result["installed"])
        recovery = result.get("watcher_recovery", {})
        self.assertTrue(recovery.get("attempted"), "recovery should have been attempted")
        self.assertTrue(recovery.get("recovered"), "recovery should have succeeded")
        self.assertIn("vllm/renderers/inputs/preprocess.py", recovery.get("missed", []), "rescan should catch the edit made during outage")

        # The watcher is now healthy
        state = bootstrap.state()
        self.assertFalse(state["watcher_failed"], "watcher should be recovered")
        self.assertTrue(state["watcher_alive"], "new watcher thread should be alive")
        self.assertEqual(state["watcher_restarts"], 1)
        self.assertFalse(state["watcher_recovery_exhausted"])

        # The rescan queued it, so it should have published
        self.assertEqual(len(result["published"]), 1)
        self.assertEqual(result["published"][0]["path"], "vllm/renderers/inputs/preprocess.py")
        self.assertEqual(provider_module.extract_prompt_components(), "V2", "the edit made during outage is now live")
        self.assertEqual(consumer_module.add_request(), "V2", "forced dependent also re-executed")

    def test_watcher_recovery_exhausted_stays_fail_closed(self):
        """Once restart budget is exhausted, watcher stays failed and sync_pending reports it."""
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        self.set_env("HMR_VLLM_MAX_WATCHER_RESTARTS", "0")  # exhaust immediately
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        provider_module = importlib.import_module("vllm.renderers.inputs.preprocess")
        self.assertEqual(provider_module.extract_prompt_components(), "V1")

        # An edit the watcher did observe and queue, before it died. This is what must survive a
        # refused boundary: an edit made *after* the death is never queued at all (nothing is
        # watching), so the queue would be trivially empty and prove nothing about discarding.
        self.provider.write_text(PROVIDER_V2, encoding="utf-8")
        bootstrap._PENDING[self.provider.resolve()] = {"path": "vllm/renderers/inputs/preprocess.py", "seen_at": 0.0}  # noqa: SLF001

        # Kill the watcher
        bootstrap._stop_watcher()  # noqa: SLF001
        with bootstrap._STATE_LOCK:  # noqa: SLF001
            bootstrap._WATCHER_FAILED = True  # noqa: SLF001

        # sync_pending sees the budget is already exhausted
        result = bootstrap.sync_pending()
        recovery = result.get("watcher_recovery", {})
        self.assertFalse(recovery.get("attempted"), "recovery should not attempt when budget exhausted")
        self.assertFalse(recovery.get("recovered"))
        self.assertTrue(recovery.get("exhausted"), "recovery should report exhausted")

        # Terminal, and said so at the top level: `installed` still records that the runtime was
        # installed in this process, so it cannot double as "HMR still works". A boundary reading
        # only `installed` would keep treating a blind process as a working one.
        self.assertTrue(result["installed"], "the runtime was installed; that fact does not change")
        # Presence and falsity asserted separately: a missing key reads as `None`, which is falsy, so
        # a bare truthiness check would pass on a result that never mentions availability at all.
        # The middleware reads this field to decide the fan-out, so the refusal must be stated.
        self.assertIn("hmr_available", result, "the refusal must be stated, not left to a missing key")
        self.assertFalse(result["hmr_available"], "a process that cannot observe changes must not claim HMR is available")

        # Watcher stays failed
        state = bootstrap.state()
        self.assertTrue(state["watcher_failed"])
        self.assertFalse(state["watcher_alive"])
        self.assertTrue(state["watcher_recovery_exhausted"])

        # Nothing was published
        self.assertEqual(len(result["published"]), 0)
        self.assertEqual(provider_module.extract_prompt_components(), "V1", "edit not published when recovery exhausted")

        # The queue is left undrained: a refused boundary must not silently discard the edit it
        # declined to publish. Were it drained, nothing on disk would ever re-announce that edit,
        # so a later fix to the watcher would come back to a queue that had already lost it.
        self.assertIn(self.provider.resolve(), set(bootstrap._PENDING), "the queued edit survives a refused boundary")  # noqa: SLF001

        # And it stays refused, on every later boundary, without attempting recovery again.
        again = bootstrap.sync_pending()
        self.assertIn("hmr_available", again)
        self.assertFalse(again["hmr_available"], "the refusal is terminal, not once-only")
        self.assertEqual(again["published"], [])
        self.assertEqual(provider_module.extract_prompt_components(), "V1")

    def test_exhausted_sync_pending_output_is_reported_terminal_by_the_middleware(self):
        """The real refusal `sync_pending` returns must read as terminal to `_failures`.

        Wired to the real call rather than a hand-written dict: the terminal signal moved to a
        top-level field, and a literal in the test would have kept asserting the old shape while
        the middleware read something the runtime no longer sends.
        """
        from vllm_hmr.runtime.middleware import _failures

        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        self.set_env("HMR_VLLM_MAX_WATCHER_RESTARTS", "0")
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        bootstrap._stop_watcher()  # noqa: SLF001
        with bootstrap._STATE_LOCK:  # noqa: SLF001
            bootstrap._WATCHER_FAILED = True  # noqa: SLF001

        local_sync = bootstrap.sync_pending()
        self.assertIn("hmr_available", local_sync)
        self.assertFalse(local_sync["hmr_available"], "precondition: this is the refused shape")

        problems = _failures(local_sync, None)

        self.assertEqual([problem["where"] for problem in problems], ["api"])
        self.assertTrue(problems[0]["exhausted"], "the real refused output must read as terminal")
        self.assertIn("recovery exhausted", problems[0]["error"])
        self.assertIn("no longer available", problems[0]["error"], "the message must say HMR is gone, not merely degraded")

        # A worker returning the same refused shape is reported per rank, not masked by the API's.
        worker_problems = _failures({"installed": True, "hmr_available": True, "watcher_failed": False, "published": [], "rejected": []}, [dict(local_sync)])
        self.assertEqual([problem["where"] for problem in worker_problems], ["worker[0]"])
        self.assertTrue(worker_problems[0]["exhausted"])

    def test_watcher_failure_is_observed_in_middleware_telemetry(self):
        """Middleware `_failures` reports a still-failed watcher, per process, and flags a terminal one.

        The API result and each worker result carry their own watcher state, so a healthy API
        process must not mask a worker whose watcher died, and an exhausted budget must be
        distinguishable from a failure recovery may still clear.
        """
        from vllm_hmr.runtime.middleware import _failures

        local_sync = {"installed": True, "deferred": False, "watcher_failed": True, "watcher_recovery": {"attempted": True, "recovered": False}, "published": [], "rejected": []}
        worker_sync = [
            {"installed": True, "deferred": False, "watcher_failed": False, "published": [], "rejected": []},
            {"installed": True, "deferred": False, "watcher_failed": True, "watcher_recovery": {"attempted": False, "recovered": False, "exhausted": True}, "published": [], "rejected": []},
        ]

        problems = _failures(local_sync, worker_sync)

        api_problems = [problem for problem in problems if problem.get("where") == "api"]
        self.assertEqual(len(api_problems), 1)
        self.assertIn("watcher failed", api_problems[0]["error"])
        self.assertFalse(api_problems[0]["exhausted"], "a spent attempt is still retryable, so it must not read as terminal")

        self.assertEqual([problem["where"] for problem in problems if problem["where"].startswith("worker")], ["worker[1]"], "rank 0 is healthy and must not be reported")
        worker_problem = next(problem for problem in problems if problem["where"] == "worker[1]")
        self.assertTrue(worker_problem["exhausted"])
        self.assertIn("recovery exhausted", worker_problem["error"])

    def test_a_recovered_watcher_is_not_reported_as_a_problem(self):
        """The recovery path runs on every boundary, so a successful one must stay silent."""
        from vllm_hmr.runtime.middleware import _failures

        local_sync = {
            "installed": True,
            "deferred": False,
            "watcher_failed": False,
            "watcher_recovery": {"attempted": True, "recovered": True, "missed": ["vllm/renderers/inputs/preprocess.py"]},
            "published": [],
            "rejected": [],
        }

        self.assertEqual(_failures(local_sync, None), [])

    def test_transient_recoveries_do_not_accumulate_toward_exhaustion(self):
        """A watcher that recovers cleanly hands the budget back; only consecutive failures exhaust it.

        Before this was fixed, the restart counter was a process-lifetime total, so a long-lived
        server that recovered three separate transient stalls (an NFS hiccup, an editor rename
        storm) days apart would refuse to recover the fourth and permanently disable HMR, even
        though the watcher was healthy the entire time in between. The budget exists to stop a
        cause that is *still true* from being retried once per request, and that case is
        unaffected: a watcher dying before any boundary sees it alive never reaches the reset.
        """
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.set_env("HMR_VLLM_SOURCE_ROOT", str(self.root))
        self.set_env("HMR_VLLM_MAX_WATCHER_RESTARTS", "3")
        bootstrap.install_from_env()
        self.addCleanup(bootstrap._stop_watcher)  # noqa: SLF001

        # Both modules: publication re-executes the forced dependent, and one that was never
        # imported is a rejection (`forced dependent ... is not loaded`), not a publication.
        provider_module = importlib.import_module("vllm.renderers.inputs.preprocess")
        consumer_module = importlib.import_module("vllm.v1.engine.async_llm")
        self.assertEqual(provider_module.extract_prompt_components(), "V1")
        self.assertEqual(consumer_module.add_request(), "V1")

        # Five transient failures, each followed by a healthy boundary that observes recovery.
        for cycle in range(1, 6):
            # Kill the watcher: this is the transient stall.
            bootstrap._stop_watcher()  # noqa: SLF001
            with bootstrap._STATE_LOCK:  # noqa: SLF001
                bootstrap._WATCHER_FAILED = True  # noqa: SLF001

            # Edit on disk while dead.
            self.provider.write_text(f"def extract_prompt_components():\n    return 'V{cycle + 1}'\n", encoding="utf-8")

            # First boundary: recovers and rescans, catching the edit made during the outage.
            result = bootstrap.sync_pending()
            self.assertTrue(result["installed"])
            self.assertTrue(result.get("hmr_available"), f"cycle {cycle} must not refuse after transient recovery")
            recovery = result.get("watcher_recovery", {})
            self.assertTrue(recovery.get("recovered"), f"cycle {cycle} recovery must succeed")
            # The rescan queued it, so this boundary publishes it.
            self.assertEqual(len(result["published"]), 1, f"cycle {cycle}: rescan must catch the edit made during outage")

            # Second boundary: observes a healthy watcher, which is what hands the budget back.
            bootstrap.sync_pending()
            state = bootstrap.state()
            self.assertEqual(state["watcher_restarts"], 0, f"cycle {cycle}: budget must be restored after a boundary sees the watcher alive")
            self.assertFalse(state["watcher_failed"])
            self.assertTrue(state["watcher_alive"])

        # All five cycles published successfully; the sixth would too.
        self.assertEqual(provider_module.extract_prompt_components(), "V6")


if __name__ == "__main__":
    unittest.main()
