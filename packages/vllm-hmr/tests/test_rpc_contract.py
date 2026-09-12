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
from unittest import mock

from vllm_hmr.runtime import middleware, telemetry
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


class ConcurrentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Two requests arriving together must not both cross the boundary as if they were alone.

    `sync_pending`'s deferral and the worker RPC are both decided from `active_scopes()`, and
    `await collective_rpc(...)` suspends between that decision and `scope_enter`. A second request
    reaching the boundary during that suspension used to read zero too, so both published and both
    fanned out to the workers, with the second publication landing while the first request was
    already being served: exactly the mid-request swap the boundary exists to prevent.
    """

    def setUp(self):
        self.assertEqual(telemetry.active_scopes(), 0)
        self.addCleanup(lambda: self.assertEqual(telemetry.active_scopes(), 0))

    async def test_the_second_request_sees_the_first_one_in_flight(self):
        in_rpc, release_rpc, release_app = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observed: list[int] = []  # `active_scopes()` as each request's boundary saw it

        class BlockingEngine:
            def __init__(self):
                self.calls: list[str] = []

            async def collective_rpc(self, method: str, timeout=None, args=(), kwargs=None):
                self.calls.append(method)
                in_rpc.set()
                await release_rpc.wait()  # hold the boundary open exactly where the race used to happen
                return [{"installed": True}]

        class BlockingApp:
            """Stays in flight, so the second boundary really is concurrent with a live request."""

            def __init__(self):
                self.active_inside: list[int] = []

            async def __call__(self, scope, receive, send):
                self.active_inside.append(telemetry.active_scopes())
                await release_app.wait()

        def recording_sync_pending():  # the middleware calls it with no arguments
            observed.append(telemetry.active_scopes())
            return {"installed": True, "published": [], "rejected": []}

        engine = BlockingEngine()
        scope: dict[str, Any] = {"type": "http", "path": "/v1/completions", "app": FakeApp(engine)}
        app = BlockingApp()
        instance = HMRBoundaryMiddleware(app)  # one instance serves every request: Starlette builds the middleware stack once
        with mock.patch.object(middleware, "sync_pending", recording_sync_pending):
            first = asyncio.create_task(instance(dict(scope), None, None))
            await in_rpc.wait()
            second = asyncio.create_task(instance(dict(scope), None, None))
            # Let the second task run until it can make no further progress on its own. Serialised,
            # that is the boundary lock; unserialised, it is already past its own worker RPC.
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertEqual(observed, [0], "the second request must not publish while the first is still at the boundary")
            release_rpc.set()
            for _ in range(10):
                await asyncio.sleep(0)
            release_app.set()
            await asyncio.gather(first, second)

        self.assertEqual(observed, [0, 1], "the second boundary must run after the first request entered its scope, not beside it")
        self.assertEqual(engine.calls, ["vllm_hmr_sync_pending"], "the second request must not fan out to the workers while the first is in flight")
        self.assertEqual(app.active_inside, [1, 2])


if __name__ == "__main__":
    unittest.main()
