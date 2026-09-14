from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch

from test_smoke_helpers import smoke


class SmokeRunTests(unittest.TestCase):
    """Drives `smoke.run` with the HTTP, RPC and distribution layers faked.

    This starts no vLLM and no container, so it is evidence about the runner's own
    assertions and cleanup only, never about real inference or real HMR behaviour.
    """

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source, self.results, self.installed = (self.root / name for name in ("source", "results", "installed"))
        for root in (self.source, self.installed):
            for relative, text in {
                smoke.TARGET: "def extract_prompt_components(x):\n    return x\n",
                smoke.DEPENDENT_PATH: "from vllm.renderers.inputs.preprocess import extract_prompt_components\n",
                "vllm/other.py": "VALUE = 2\n",
            }.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        self.core_path = self.root / "core.py"
        self.core_path.write_text("PINNED_CORE = True\n")
        core = ModuleType("reactivity.hmr.core")
        core.__file__ = str(self.core_path)
        hmr = ModuleType("reactivity.hmr")
        hmr.core = core  # pyright: ignore[reportAttributeAccessIssue]
        self.enterContext(patch.dict(sys.modules, {"reactivity.hmr": hmr, "reactivity.hmr.core": core}))
        dist = SimpleNamespace(version="0.28.0+cpu", locate_file=lambda name: self.installed / name)
        self.enterContext(patch.object(smoke, "distribution", return_value=dist))
        self.args = SimpleNamespace(
            source=str(self.source),
            results=str(self.results),
            installed_source=str(self.installed / "vllm"),
            vllm_version=dist.version,
            pyth_core_path=str(self.core_path),
            pyth_core_sha256=smoke.sha256(self.core_path),
            model="facebook/opt-125m",
            port=28473,
            startup_timeout=1,
            image="test-image",
            image_id="test-id",
            image_digest="test-digest",
        )
        self.process = Mock(pid=424242, returncode=None)
        self.process.poll.side_effect = lambda: self.process.returncode
        self.process.wait.side_effect = self.wait
        # Replace only the Popen that smoke.py itself resolves: smoke.subprocess is the same
        # module object as the one imported here, and patching it globally would also hijack
        # the subprocess.run this test uses to verify argv independently.
        self.popen = self.enterContext(
            patch.object(smoke, "subprocess", SimpleNamespace(Popen=Mock(return_value=self.process), STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))
        ).Popen
        # `os.killpg` is POSIX-only, so `create=True` lets the patch succeed on Windows where it doesn't exist.
        self.killpg = self.enterContext(patch.object(smoke.os, "killpg", create=True))
        self.enterContext(patch.object(smoke.shutil, "which", return_value="/trusted/vllm-hmr"))
        self.enterContext(patch.object(smoke, "wait_ready"))
        self.enterContext(patch.object(smoke, "state", side_effect=self.snapshot))
        self.enterContext(
            patch.object(
                smoke,
                "wait_published",
                return_value=(
                    {"path": "/__hmr__/state", "t": 2},
                    {"path": smoke.TARGET, "forced_dependents_reexecuted": [smoke.DEPENDENT]},
                    {"path": smoke.TARGET, "t": 1},
                ),
            )
        )
        self.enterContext(patch.object(smoke, "wait_worker_queued", return_value=[{"pid": 424243, "path": smoke.TARGET}]))
        self.completions = self.enterContext(patch.object(smoke, "completion", side_effect=self.complete))
        self.before_baseline = smoke.python_source_hashes(self.source)
        self.emit_marker = True
        self.decision = {"installed": True, "published": [], "rejected": [{"path": smoke.TARGET, "error": smoke.WORKER_OUT_OF_MAP}]}
        self.api_pid = self.process.pid
        self.parameters = [{"name": "weight", "data_ptr": 987654, "shape": [8, 8], "dtype": "torch.float32"}]

    def wait(self, timeout):
        del timeout  # keyword name must match Popen.wait(timeout=...), which smoke.py calls
        self.process.returncode = -15  # SIGTERM; negative convention matches POSIX waitpid's killed-by-signal return
        return self.process.returncode

    def complete(self, base, model):
        del base, model
        with (self.results / "cpu-smoke-full.log").open("a") as log:
            if self.completions.call_count == 1:
                log.write("Starting to load model\n")
            elif self.emit_marker:
                log.write(f"{smoke.MARKER} pid={self.api_pid}\n")
        return 200, {}, {"id": "test-completion", "choices": [{"text": "41"}]}

    def snapshot(self, base):
        del base
        manifest = json.loads((self.results / "cpu-source-manifest.json").read_text())
        argv = self.popen.call_args.args[0][5:]
        telemetry = {
            "pid": self.api_pid,
            "orig_argv": ["python3", "/trusted/vllm", *argv, "--middleware", smoke.PACKAGE_MIDDLEWARE],
            "events": [
                {"kind": "request_boundary", "path": "/v1/completions", "worker_sync": [self.decision]},
            ],
        }
        return {
            "api": {"installed": True, "manifest": manifest, "telemetry": telemetry},
            "workers": [
                {
                    "pid": 424243,
                    "rank": 0,
                    "model_id": 123,
                    "model_class_id": 456,
                    "model_class": "OPTForCausalLM",
                    "parameter_sample": copy.deepcopy(self.parameters),
                    "hmr": {"installed": True, "manifest": manifest},
                }
            ],
        }

    def receipt(self):
        return json.loads((self.results / "cpu-smoke-receipt.json").read_text())

    def test_success_requires_restoration_and_teardown(self):
        smoke.run(self.args)
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(all(receipt["assertions"].values()))
        self.assertTrue(receipt["server_stopped"])
        self.assertEqual(smoke.python_source_hashes(self.source), self.before_baseline)
        self.assertEqual(self.completions.call_count, 2)
        # SIGTERM alone brought the leader down, so the group must not be SIGKILLed: escalating
        # anyway would cut off an engine/worker still flushing its own teardown evidence.
        self.assertEqual(self.killpg.call_args_list, [call(self.process.pid, signal.SIGTERM)])

    def test_rejects_worker_claiming_api_only_publication(self):
        self.decision.update(published=[{"path": smoke.TARGET}], rejected=[])
        with self.assertRaisesRegex(AssertionError, "outside its module map"):
            smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "failed")
        self.assertTrue(self.receipt()["source_restored"])

    def test_preexisting_marker_is_not_post_edit_evidence(self):
        self.emit_marker = False
        complete = self.complete

        def stale_marker(base, model):
            result = complete(base, model)
            if self.completions.call_count == 1:
                with (self.results / "cpu-smoke-full.log").open("a") as log:
                    log.write(f"{smoke.MARKER} pid={self.api_pid}\n")
            return result

        self.completions.side_effect = stale_marker
        with self.assertRaisesRegex(AssertionError, "marker missing"):
            smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "failed")

    def test_deleted_non_target_source_fails_restoration(self):
        complete = self.complete

        def delete_other(base, model):
            if self.completions.call_count == 2:
                (self.source / "vllm/other.py").unlink()
            return complete(base, model)

        self.completions.side_effect = delete_other
        with self.assertRaises(AssertionError):
            smoke.run(self.args)
        self.assertFalse(self.receipt()["source_restored"])
        self.assertEqual(self.receipt()["changed_python_sources_after_restore"], ["vllm/other.py"])

    def test_unrelated_api_pid_is_rejected(self):
        self.api_pid += 1
        with self.assertRaisesRegex(AssertionError, "API PID"):
            smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "failed")

    def test_empty_parameter_evidence_is_rejected(self):
        self.parameters = []
        with self.assertRaisesRegex(AssertionError, "parameter evidence"):
            smoke.run(self.args)

    def test_server_exit_before_teardown_cannot_pass(self):
        complete = self.complete

        def exited(base, model):
            if self.completions.call_count == 2:
                self.process.returncode = 9
            return complete(base, model)

        self.completions.side_effect = exited
        with self.assertRaisesRegex(AssertionError, "server exited"):
            smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "failed")
        # A dead leader does not mean an empty group, so the group is still signalled; the
        # leader reaped without a timeout, so there is nothing to escalate against.
        self.assertEqual(self.killpg.call_args_list, [call(self.process.pid, signal.SIGTERM)])

    def test_missing_process_group_does_not_lose_receipt(self):
        self.killpg.side_effect = ProcessLookupError
        smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "passed")

    def test_teardown_timeout_records_failure_and_nonzero_outcome(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired("vllm-hmr", 30)
        with self.assertRaises(AssertionError):
            smoke.run(self.args)
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("TimeoutExpired", receipt["teardown_error"])
        self.assertTrue(receipt["source_restored"])

    def test_sigkill_escalation_can_still_succeed(self):
        def wait(timeout):
            if timeout == 60:
                raise subprocess.TimeoutExpired("vllm-hmr", timeout)
            return self.wait(timeout)

        self.process.wait.side_effect = wait
        smoke.run(self.args)
        self.assertEqual(self.receipt()["status"], "passed")

    def test_signal_during_teardown_still_writes_the_receipt(self):
        # `interrupted` raises KeyboardInterrupt on SIGTERM, and `docker stop` sends a second one
        # when the first teardown is slow. That BaseException must not escape the receipt write.
        self.killpg.side_effect = KeyboardInterrupt("received signal 15")
        with self.assertRaises(AssertionError):
            smoke.run(self.args)
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("KeyboardInterrupt", receipt["teardown_error"])
        self.assertTrue(receipt["source_restored"])

    def test_primary_failure_survives_teardown_error(self):
        self.completions.side_effect = AssertionError("primary failure")
        self.killpg.side_effect = PermissionError("teardown failure")
        with self.assertRaisesRegex(AssertionError, "primary failure"):
            smoke.run(self.args)
        receipt = self.receipt()
        self.assertIn("primary failure", receipt["error"])
        self.assertIn("teardown failure", receipt["teardown_error"])

    def test_release_mismatches_fail_before_popen_with_receipt_and_log(self):
        cases = ("target", "dependent", "changed", "added", "deleted", "version", "installed_path", "core_path", "core_hash")
        for case in cases:
            with self.subTest(case=case):
                self.args.results = str(self.root / f"results-{case}")
                with ExitStack() as stack:
                    if case in ("target", "dependent", "deleted"):
                        path = self.source / {"target": smoke.TARGET, "dependent": smoke.DEPENDENT_PATH, "deleted": "vllm/other.py"}[case]
                        original = path.read_bytes()
                        stack.callback(path.write_bytes, original)
                        path.unlink()
                    elif case == "changed":
                        path = self.source / "vllm/other.py"
                        stack.callback(path.write_bytes, path.read_bytes())
                        path.write_text("VALUE = 99\n")
                    elif case == "added":
                        path = self.source / "vllm/new.py"
                        stack.callback(path.unlink)
                        path.write_text("VALUE = 3\n")
                    else:
                        attribute = {"version": "vllm_version", "installed_path": "installed_source", "core_path": "pyth_core_path", "core_hash": "pyth_core_sha256"}[case]
                        stack.enter_context(patch.object(self.args, attribute, "wrong"))
                    with self.assertRaisesRegex((FileNotFoundError, AssertionError), "release/source mismatch"):
                        smoke.run(self.args)
                    receipt = json.loads((Path(self.args.results) / "cpu-smoke-receipt.json").read_text())
                    self.assertEqual(receipt["status"], "failed")
                    self.assertTrue((Path(self.args.results) / "cpu-smoke-full.log").is_file())
                    self.popen.assert_not_called()

    def test_missing_launcher_preserves_failure_receipt(self):
        with patch.object(smoke.shutil, "which", return_value=None), self.assertRaises(FileNotFoundError):
            smoke.run(self.args)
        self.assertIn("console script", self.receipt()["error"])
        self.popen.assert_not_called()

    def test_existing_artifacts_are_never_overwritten(self):
        self.results.mkdir()
        for artifact in ("cpu-smoke-receipt.json", "cpu-smoke-full.log", "cpu-source-manifest.json"):
            with self.subTest(artifact=artifact):
                path = self.results / artifact
                path.write_bytes(b"existing evidence")
                with self.assertRaises(FileExistsError):
                    smoke.run(self.args)
                self.assertEqual(path.read_bytes(), b"existing evidence")
        self.popen.assert_not_called()

    def test_environment_cannot_override_default_runtime(self):
        poisoned = {"HMR_VLLM_RUNTIME": "other:install", "HMR_VLLM_DISABLED": "1", "HMR_VLLM_SKIP": "1", "HMR_VLLM_ENABLE": "1"}
        with patch.dict(os.environ, poisoned):
            smoke.run(self.args)
        env = self.popen.call_args.kwargs["env"]
        self.assertTrue(set(poisoned).isdisjoint(env))

    def test_popen_preserves_shell_metacharacters_as_one_argument(self):
        payload = "opt-125m; $(touch INJECTED) `touch INJECTED` 'quoted' &"
        self.args.model = payload
        smoke.run(self.args)
        command = self.popen.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[command.index("serve") + 1], payload)
        self.assertFalse(self.popen.call_args.kwargs.get("shell", False))
        # Executes the argv smoke.py actually built, against a harmless local program, so this is
        # not a second safe command assembled by the test and then asserted against itself.
        executable = self.root / "capture.py"
        executable.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        result = subprocess.run([sys.executable, str(executable), *command[1:]], cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), command[1:])
        self.assertFalse((self.root / "INJECTED").exists())


if __name__ == "__main__":
    unittest.main()
