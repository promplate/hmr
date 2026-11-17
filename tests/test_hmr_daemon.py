import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


@unittest.skipUnless(os.name == "posix", "POSIX daemon handoff")
class DaemonHandoffTests(unittest.TestCase):
    def run_case(self, code, *, disabled=False):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.py").touch()
            env = {key: value for key, value in os.environ.items() if key != "NO_HMR_DAEMON"}
            if disabled:
                env["NO_HMR_DAEMON"] = "1"
            result = subprocess.run(
                [sys.executable, "-c", dedent(code)],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_reloader_stops_automatic_daemon(self):
        self.run_case("""\
            import threading
            import hmr_daemon.posix.main as daemon
            from hmr_daemon.posix import worker
            from reactivity.hmr.core import BaseReloader

            watcher = next(thread for thread in threading.enumerate() if thread.name == "hmr-daemon")
            BaseReloader("main.py")
            if not daemon.shutdown_event.is_set():
                raise RuntimeError("automatic daemon remained active after explicit reloader construction")
            watcher.join(5)
            if watcher.is_alive():
                raise RuntimeError("automatic watcher did not stop on an idle pipe")
            worker.wait(5)
            """)

    def test_handoff_before_watcher_thread_starts(self):
        self.run_case(
            """\
            import importlib
            import os
            import threading
            import hmr_daemon
            from reactivity.hmr.core import BaseReloader

            original_start = threading.Thread.start
            deferred = []
            threading.Thread.start = lambda thread: deferred.append(thread)
            try:
                os.environ.pop("NO_HMR_DAEMON", None)
                importlib.reload(hmr_daemon)
                import hmr_daemon.posix.main as daemon
                from hmr_daemon.posix import worker
                BaseReloader("main.py")
                if not daemon.shutdown_event.is_set():
                    raise RuntimeError("explicit reloader was constructed before the handoff hook")
            finally:
                threading.Thread.start = original_start
                for thread in deferred:
                    thread.start()

            for thread in deferred:
                thread.join(5)
                if thread.is_alive():
                    raise RuntimeError("automatic watcher remained active after early handoff")
            worker.wait(5)
            """,
            disabled=True,
        )
