"""The middleware/worker contract: every RPC the API process emits must exist on the worker.

These two halves live in different processes and are wired together only by a string in
`collective_rpc`, so nothing but a test keeps them in sync. vLLM resolves the name with
`getattr(worker, name)` (`vllm/v1/serial_utils.py: run_method`), which is what is checked
here against a fake engine client, without starting vLLM.
"""

from __future__ import annotations

# ruff: noqa: ARG002, ASYNC109  # the fakes here must mirror vLLM's and ASGI's real signatures, unused parameters included
import asyncio
import inspect
import unittest
from typing import Any

from vllm_hmr.runtime import telemetry
from vllm_hmr.runtime.middleware import HMRBoundaryMiddleware
from vllm_hmr.runtime.worker import HMRWorkerExtension


class FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    async def collective_rpc(self, method: str, timeout=None, args=(), kwargs=None):
        self.calls.append((method, args, kwargs or {}))
        return [{"installed": True}]


class FakeState:
    def __init__(self, engine):
        self.engine_client = engine


class FakeApp:
    def __init__(self, engine):
        self.state = FakeState(engine)


class RecordingApp:
    def __init__(self):
        self.scopes: list[dict] = []
        self.scopes_active_inside: list[int] = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        self.scopes_active_inside.append(telemetry.active_scopes())


def run_middleware(path: str, engine=None, scope_type: str = "http"):
    app = RecordingApp()
    scope: dict[str, Any] = {"type": scope_type, "path": path}
    if engine is not None:
        scope["app"] = FakeApp(engine)
    asyncio.run(HMRBoundaryMiddleware(app)(scope, None, None))
    return app


class RPCContractTests(unittest.TestCase):
    def test_every_rpc_name_the_middleware_emits_exists_on_the_worker_extension(self):
        engine = FakeEngine()
        run_middleware("/v1/completions", engine)
        self.assertTrue(engine.calls, "an inference request must ask the workers to publish too")
        for name, args, kwargs in engine.calls:
            method = getattr(HMRWorkerExtension, name, None)
            self.assertTrue(callable(method), f"middleware emits {name!r}, which HMRWorkerExtension does not implement")
            # `run_method` calls `getattr(worker, name)(*args, **kwargs)`: the bound signature must accept them.
            inspect.signature(method).bind(object(), *args, **kwargs)  # pyright: ignore[reportArgumentType]

    def test_worker_rpcs_are_namespaced_so_vllm_cannot_reject_the_mixin(self):
        """`init_worker` asserts no public attribute of the extension collides with the worker class."""
        public = [name for name in dir(HMRWorkerExtension) if not name.startswith("__")]
        self.assertEqual(sorted(public), ["vllm_hmr_state", "vllm_hmr_sync_pending"])

    def test_the_declared_worker_attribute_is_annotation_only(self):
        """`rank` documents what the host worker provides; a real class attribute would trip vLLM's collision check."""
        self.assertIn("rank", HMRWorkerExtension.__annotations__)
        self.assertNotIn("rank", vars(HMRWorkerExtension))

    def test_worker_rpcs_run_without_an_install_and_report_it(self):
        """Called before/without installation they must answer, not raise inside the engine's RPC loop."""
        worker = HMRWorkerExtension()
        worker.rank = 0
        self.assertEqual(worker.vllm_hmr_sync_pending()["installed"], False)
        state = worker.vllm_hmr_state()
        self.assertEqual(state["rank"], 0)
        self.assertFalse(state["hmr"]["installed"])


class MiddlewareBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(telemetry.active_scopes(), 0)
        self.addCleanup(lambda: self.assertEqual(telemetry.active_scopes(), 0))

    def test_non_http_scopes_are_passed_through_untouched(self):
        engine = FakeEngine()
        app = run_middleware("/v1/completions", engine, scope_type="lifespan")
        self.assertEqual(engine.calls, [])
        self.assertEqual(app.scopes_active_inside, [0])  # no scope is entered, so nothing blocks publication

    def test_non_inference_paths_do_not_pay_a_collective_rpc(self):
        engine = FakeEngine()
        for path in ("/health", "/metrics", "/"):
            run_middleware(path, engine)
        self.assertEqual(engine.calls, [])

    def test_a_missing_engine_client_does_not_break_the_request(self):
        """The render server sets `state.engine_client = None`; ASGI scopes may have no app at all."""
        self.assertEqual(len(run_middleware("/v1/completions").scopes), 1)
        self.assertEqual(len(run_middleware("/v1/completions", None).scopes), 1)

    def test_the_request_is_inside_a_scope_that_blocks_publication(self):
        app = run_middleware("/v1/completions", FakeEngine())
        self.assertEqual(app.scopes_active_inside, [1])

    def test_a_scope_is_left_even_when_the_application_raises(self):
        class Failing:
            async def __call__(self, scope, receive, send):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            asyncio.run(HMRBoundaryMiddleware(Failing())({"type": "http", "path": "/v1/completions"}, None, None))


if __name__ == "__main__":
    unittest.main()
