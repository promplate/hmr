# ruff: noqa: E402  # `smoke.py` and `mutations.py` are runner scripts, not an installed package; sys.path must be set up first

from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_ROOT))
from mutations import MutationSet, print_statement

spec = importlib.util.spec_from_file_location("vllm_cpu_hmr_smoke", EXAMPLE_ROOT / "smoke.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeHelperTests(unittest.TestCase):
    def test_only_target_source_changes_and_is_restored(self):
        root = Path(tempfile.mkdtemp())
        try:
            package = root / "vllm"
            package.mkdir()
            target = package / "target.py"
            other = package / "other.py"
            original_target = b"def target(x):\n    return x\n"
            target.write_bytes(original_target)
            other.write_text("VALUE = 2\n")
            before = smoke.python_source_hashes(root)
            marker = "HMR_PROBE_VLLM_TEST_001"
            with MutationSet(root) as edits:
                line = edits.insert_statement("vllm/target.py", "target", print_statement(marker))
                self.assertEqual(line, 2)
                ast.parse(target.read_text())
                self.assertEqual(
                    [path for path, digest in smoke.python_source_hashes(root).items() if before[path] != digest],
                    ["vllm/target.py"],
                )
            self.assertEqual(target.read_bytes(), original_target)
            self.assertEqual(smoke.python_source_hashes(root), before)
        finally:
            shutil.rmtree(root)

    def test_publication_requires_forced_direct_importer(self):
        published = {
            "path": smoke.TARGET,
            "forced_dependents_reexecuted": [smoke.DEPENDENT],
        }
        boundary = {
            "kind": "request_boundary",
            "t": 2.0,
            "local_sync": {"published": [published]},
        }
        queued = {"kind": "source_change", "t": 1.0, "path": smoke.TARGET}
        snapshot = {"api": {"telemetry": {"events": [queued, boundary]}}}
        self.assertEqual(smoke.publication_for_target(snapshot), (boundary, published))
        self.assertEqual(smoke.queued_before(snapshot, boundary), queued)


if __name__ == "__main__":
    unittest.main()
