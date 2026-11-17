import os
import tempfile
import unittest
from pathlib import Path
import source_rewrite_case as case

patched = case.PACKAGE
summary = []
with tempfile.TemporaryDirectory() as directory:
    baseline = Path(directory)
    (baseline / "uvicorn_hmr.py").write_text(Path(__file__).with_name("baseline_uvicorn.txt").read_text())
    for seed in range(5):
        os.environ["PYTHONHASHSEED"] = str(seed)
        for label, package in [("baseline", baseline), ("patched", patched)]:
            print(f"COMPARE {label} seed={seed}", flush=True)
            case.PACKAGE = package
            suite = unittest.TestSuite([case.StartupWatchTests("test_blocking_startup_buffers_changes")])
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            summary.append((label, seed, result.wasSuccessful()))
print("COMPARISON_SUMMARY", summary, flush=True)
