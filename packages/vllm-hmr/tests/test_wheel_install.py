"""Packaging tests: the `vllm-hmr` console script must work from an installed wheel.

These build a real wheel and install it into a throwaway venv, because the unit
tests import the source tree and so cannot catch packaging mistakes: a missing
`[project.scripts]` entry, or `_sitecustomize/sitecustomize.py` left out of the
wheel, would keep every unit test green while the shipped package is unusable.

`uv` drives both steps: it supplies an interpreter satisfying `requires-python`,
which the ambient `python3` is not guaranteed to do.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")


def run(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env, check=False)


@unittest.skipIf(UV is None, "requires `uv` to build the wheel and provision an interpreter")
class WheelInstallTests(unittest.TestCase):
    """One wheel build + venv install shared by every check here; it costs seconds, not milliseconds."""

    tmp: tempfile.TemporaryDirectory[str]
    script: Path
    python: Path
    site_packages: Path

    @classmethod
    def setUpClass(cls):
        assert UV is not None
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        dist = root / "dist"
        venv = root / "venv"

        built = run(UV, "build", "--wheel", "--out-dir", str(dist), str(PACKAGE_ROOT))
        assert built.returncode == 0, f"uv build failed:\n{built.stdout}\n{built.stderr}"
        wheels = list(dist.glob("*.whl"))
        assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"

        # `requires-python` is >=3.12; ask uv for that rather than trusting the ambient python3.
        created = run(UV, "venv", "--python", "3.12", str(venv))
        assert created.returncode == 0, f"uv venv failed:\n{created.stdout}\n{created.stderr}"
        python = venv / "bin" / "python"
        installed = run(UV, "pip", "install", "--python", str(python), str(wheels[0]))
        assert installed.returncode == 0, f"wheel install failed:\n{installed.stdout}\n{installed.stderr}"

        cls.script = venv / "bin" / "vllm-hmr"
        cls.python = python
        cls.site_packages = next((venv / "lib").glob("python3.*")) / "site-packages"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stub_vllm_dir(self, body: str) -> Path:
        """A fake `vllm` on PATH, so nothing here needs real vLLM installed."""
        stub_dir = Path(self.tmp.name) / f"stub-{self.id().rsplit('.', 1)[-1]}"
        stub_dir.mkdir(exist_ok=True)
        stub = stub_dir / "vllm"
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
        return stub_dir

    def source_tree(self) -> Path:
        """The wrapper validates the source root before exec, so it must really exist."""
        root = Path(self.tmp.name) / f"src-{self.id().rsplit('.', 1)[-1]}"
        for relative in ("vllm/renderers/inputs/preprocess.py", "vllm/v1/engine/async_llm.py"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {relative}\n", encoding="utf-8")
        return root

    def test_console_script_is_installed_and_executable(self):
        self.assertTrue(self.script.is_file(), f"{self.script} was not created by the wheel install")
        self.assertTrue(os.access(self.script, os.X_OK))

    def test_help_runs_from_the_installed_script(self):
        done = run(str(self.script), "--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Usage: vllm-hmr", done.stdout)
        self.assertIn("Not implemented", done.stdout)  # capability boundary survives packaging

    def test_bare_invocation_exits_two(self):
        done = run(str(self.script))
        self.assertEqual(done.returncode, 2)
        self.assertIn("Usage: vllm-hmr", done.stdout)

    def test_usage_error_exits_two_with_message_on_stderr(self):
        done = run(str(self.script), "--hmr-nope", "serve", "m")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown option --hmr-nope", done.stderr)

    def test_missing_vllm_is_reported_not_traced(self):
        empty = Path(self.tmp.name) / "empty-path"
        empty.mkdir(exist_ok=True)
        env = {**os.environ, "PATH": str(empty)}
        done = run(str(self.script), "serve", "m", env=env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("`vllm` was not found on PATH", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_print_env_points_at_the_shim_inside_the_wheel(self):
        stub_dir = self.stub_vllm_dir("#!/bin/sh\nexit 0\n")
        env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
        done = run(str(self.script), "--hmr-print-env", "--hmr-source-root", str(self.source_tree()), "--hmr-runtime", "pkg.mod:entry", "serve", "m", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("HMR_VLLM_ENABLE=1", done.stdout)
        self.assertIn("HMR_VLLM_RUNTIME=pkg.mod:entry", done.stdout)
        # The shim must resolve inside the installed package, never back to this source checkout.
        shim = self.site_packages / "vllm_hmr" / "_sitecustomize"
        self.assertTrue((shim / "sitecustomize.py").is_file(), f"wheel did not ship {shim}/sitecustomize.py")
        self.assertIn(f"PYTHONPATH={shim}", done.stdout)
        self.assertNotIn(str(PACKAGE_ROOT), done.stdout)

    def test_installed_shim_injects_into_the_exec_d_interpreter(self):
        """The whole point of the package: the exec'd interpreter runs the configured runtime before vLLM."""
        root = Path(self.tmp.name)
        runtime_dir = root / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        sentinel = root / "sentinel.txt"
        sentinel.unlink(missing_ok=True)
        (runtime_dir / "probe_runtime.py").write_text(
            "import os, pathlib\n\n\ndef entry():\n    pathlib.Path(os.environ['PROBE_SENTINEL']).write_text('injected', encoding='utf-8')\n",
            encoding="utf-8",
        )
        # Stand in for vLLM's entrypoint: any interpreter start is enough to prove `site` loaded our shim.
        stub_dir = self.stub_vllm_dir(f'#!/bin/sh\nexec "{self.python}" -c "print(\'vllm-started\')"\n')
        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(runtime_dir),
            "PROBE_SENTINEL": str(sentinel),
        }
        done = run(str(self.script), "--hmr-source-root", str(self.source_tree()), "--hmr-runtime", "probe_runtime:entry", "serve", "m", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("vllm-started", done.stdout)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "injected")

    def test_default_serve_injects_the_packaged_runtime_and_vllm_flags(self):
        """`vllm-hmr serve MODEL` with no HMR option must still arrive fully wired."""
        stub_dir = self.stub_vllm_dir('#!/bin/sh\necho "argv: $@"\nexit 0\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith("HMR_VLLM_")}
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        done = run(str(self.script), "--hmr-print-env", "--hmr-source-root", str(self.source_tree()), "serve", "facebook/opt-125m", "--port", "8000", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("HMR_VLLM_RUNTIME=vllm_hmr.runtime.bootstrap:install_unless_registry_inspector", done.stdout)
        self.assertIn("--middleware vllm_hmr.runtime.middleware.HMRBoundaryMiddleware", done.stdout)
        self.assertIn("--worker-extension-cls vllm_hmr.runtime.worker.HMRWorkerExtension", done.stdout)
        self.assertIn("serve facebook/opt-125m --port 8000", done.stdout)

    def test_runtime_modules_are_importable_from_the_wheel(self):
        """The default runtime is only a default if the wheel actually ships it."""
        done = run(
            str(self.python),
            "-c",
            "from vllm_hmr.runtime import bootstrap, middleware, scope, telemetry, worker; "
            "print(scope.REACTIVE_PATHS); print(bootstrap.install_unless_registry_inspector.__name__); "
            "print(middleware.HMRBoundaryMiddleware.__name__, worker.HMRWorkerExtension.__name__, telemetry.active_scopes())",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("vllm/renderers/inputs/preprocess.py", done.stdout)
        self.assertIn("vllm/v1/engine/async_llm.py", done.stdout)

    def test_site_packages_install_without_source_root_fails_with_guidance(self):
        stub_dir = self.stub_vllm_dir("#!/bin/sh\nexit 0\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("HMR_VLLM_")}
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        done = run(str(self.script), "serve", "m", env=env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--hmr-source-root", done.stderr)
        self.assertIn("--hmr-disabled", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_skip_marker_suppresses_injection_after_install(self):
        root = Path(self.tmp.name)
        runtime_dir = root / "runtime-skip"
        runtime_dir.mkdir(exist_ok=True)
        sentinel = root / "skip-sentinel.txt"
        sentinel.unlink(missing_ok=True)
        (runtime_dir / "probe_runtime_skip.py").write_text(
            "import os, pathlib\n\n\ndef entry():\n    pathlib.Path(os.environ['PROBE_SENTINEL']).write_text('injected', encoding='utf-8')\n",
            encoding="utf-8",
        )
        stub_dir = self.stub_vllm_dir(f'#!/bin/sh\nexec "{self.python}" -c "print(\'vllm-started\')"\n')
        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(runtime_dir),
            "PROBE_SENTINEL": str(sentinel),
            "HMR_VLLM_SKIP": "1",
        }
        done = run(str(self.script), "--hmr-source-root", str(self.source_tree()), "--hmr-runtime", "probe_runtime_skip:entry", "serve", "m", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("vllm-started", done.stdout)
        self.assertFalse(sentinel.exists(), "HMR_VLLM_SKIP must keep the runtime from being invoked")

    def report_env_stub_dir(self) -> Path:
        """A stub `vllm` that reports what the exec'd interpreter actually inherited."""
        return self.stub_vllm_dir(
            f"#!/bin/sh\necho \"argv: $@\"\nexec \"{self.python}\" -c \"import os; print(os.environ.get('PYTHONPATH', '<unset>')); print(sorted(k for k in os.environ if k.startswith('HMR_VLLM_')))\"\n"
        )

    def test_disabled_launch_does_not_inject(self):
        """`--hmr-disabled` is the documented opt-out: no shim on PYTHONPATH, no HMR_VLLM_* exported, no vLLM flags added."""
        stub_dir = self.report_env_stub_dir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("HMR_VLLM_")}
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        env.pop("PYTHONPATH", None)
        done = run(str(self.script), "--hmr-disabled", "serve", "m", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("argv: serve m\n", done.stdout)
        self.assertIn("<unset>", done.stdout)
        self.assertIn("[]", done.stdout)

    def test_non_serve_subcommand_is_untouched(self):
        stub_dir = self.report_env_stub_dir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("HMR_VLLM_")}
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
        env.pop("PYTHONPATH", None)
        done = run(str(self.script), "chat", "--url", "http://localhost:8000", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("argv: chat --url http://localhost:8000\n", done.stdout)
        self.assertIn("<unset>", done.stdout)
        self.assertIn("[]", done.stdout)


if __name__ == "__main__":
    unittest.main()
